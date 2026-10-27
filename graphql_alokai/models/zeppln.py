# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json
import logging
import time

import requests

from odoo import api, fields, models
from odoo.tools import SQL

from .website import DIRTY_BATCH, slug_key

_logger = logging.getLogger(__name__)


class AlokaiZepplnDirty(models.Model):
    """Stock and slugs the Zeppln storefront has yet to receive: the Redis
    dirty flags, in Postgres, as Odoo has no Redis with Zeppln.

    Rows are only ever added - one per change, the same product as often as
    it changes, so concurrent saves never wait on each other - and in the
    transaction of the change, so a rolled-back save leaves none. The dirty
    cron sends them and deletes what it sent.
    """
    _name = 'alokai.zeppln.dirty'
    _description = 'Alokai Zeppln Dirty'
    _log_access = False

    kind = fields.Char(required=True)  # 'stock' or 'slug'
    # {'ids': [...]}, plus 'model' and 'previous' for slugs: see
    # website._alokai_mark_slugs_dirty.
    entry = fields.Json(required=True)

    def init(self):
        # Rows come and go all day in a table that stays near empty: vacuum
        # on a count of dead rows rather than a share of the table.
        self.env.cr.execute(SQL(
            "ALTER TABLE %s SET (autovacuum_vacuum_scale_factor = 0, autovacuum_vacuum_threshold = 1000)",
            SQL.identifier(self._table)))

    @api.model
    def _add(self, kind, entries):
        # Plain SQL: this runs in every stock move and product save.
        self.env.cr.execute(SQL("INSERT INTO %s (kind, entry) VALUES %s", SQL.identifier(self._table), SQL(', ').join(
            SQL('(%s, %s)', kind, json.dumps(entry)) for entry in entries)))

    @api.model
    def _send(self):
        """Send the rows to the storefront, a batch at a time, until none are
        left or the run has had its time.

        A batch is deleted only once the storefront took it, and committed on
        its own. Stock is read in the snapshot the rows were read in, so it
        includes every change they stand for; a change committed since stays
        for the next batch. On a failure the batch is kept and the run stops,
        to try again on the next.
        """
        website = self.env['website']._zeppln_website()
        if not website:
            return
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            self.env.cr.execute(SQL(
                "SELECT id, kind, entry FROM %s ORDER BY id LIMIT %s",
                SQL.identifier(self._table), DIRTY_BATCH))
            rows, size = [], 0
            for row in self.env.cr.fetchall():
                rows.append(row)
                size += len(row[2]['ids'])
                if size >= DIRTY_BATCH:
                    break
            if not rows:
                return

            product_ids = {pid for _id, kind, entry in rows if kind == 'stock' for pid in entry['ids']}
            slug_entries = [entry for _id, kind, entry in rows if kind == 'slug']
            try:
                if product_ids:
                    self.env['product.product'].sudo().browse(product_ids).exists() \
                        ._update_products_stock_redis(None)
                if slug_entries:
                    website._zeppln_post('slugs', {'slugs': [
                        # Keys, not slugs: the storefront would not encode
                        # them exactly as Odoo does.
                        {'model': model,
                         'set': sorted(map(slug_key, current)),
                         'delete': sorted(map(slug_key, gone - current))}
                        for model, (current, gone)
                        in self.env['website']._alokai_slug_changes(slug_entries).items()
                    ]})
            except requests.RequestException as error:
                self.env.cr.rollback()
                self.env.invalidate_all()
                _logger.warning("Zeppln: sending to %s failed, retrying on the next run: %s",
                                website.alokai_domain, error)
                return

            self.env.cr.execute(SQL(
                "DELETE FROM %s WHERE id = ANY(%s)", SQL.identifier(self._table), [row[0] for row in rows]))
            self.env.cr.commit()
            self.env.invalidate_all()
