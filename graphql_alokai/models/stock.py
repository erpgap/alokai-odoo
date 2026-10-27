# -*- coding: utf-8 -*-
# Copyright 2025 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import uuid
from odoo import tools, models, api, _


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _create_stock_is_dirty_redis(self):
        # Opt-in; skip entirely when there's nowhere to send stock (also
        # covers tests).
        mode = self.env['website']._alokai_sync_mode()
        if not mode:
            return

        product_ids = list({q.product_id.id for q in self})
        if not product_ids:
            return
        if mode == 'redis':
            # Defer to postcommit so the dirty flag only appears after Postgres
            # commits, otherwise the cron could read it and write stale stock.
            self.env.cr.postcommit.add(lambda: self._write_dirty_keys_redis(product_ids))
        else:
            # In the same transaction: the row is only seen once it commits.
            self.env['alokai.zeppln.dirty']._add('stock', [{'ids': product_ids}])

    @api.model
    def _write_dirty_keys_redis(self, product_ids):
        redis_client = self.env['website']._redis_connect()
        if not redis_client:
            return
        try:
            # uuid suffix so the cron only deletes the keys it scanned;
            # flags created mid-run survive for the next cycle.
            suffix = uuid.uuid4().hex
            pipe = redis_client.pipeline()
            for product_id in product_ids:
                pipe.set(f'stock:product-is-dirty-{product_id}-{suffix}', product_id)
            pipe.execute()
        finally:
            redis_client.close()

    def _trigger_dirty_stock_cron(self):
        # A quant change just altered free_qty. Ask the dirty-stock cron to run
        # as soon as possible instead of waiting for its next tick, so the
        # storefront's Redis stock reflects the change within seconds.
        #
        # We only trigger the existing cron (never write the redis_stock tables
        # here) to keep it the single writer to those tables and avoid the
        # serialization errors that concurrent writers would cause.
        if not self.env['website']._alokai_sync_mode():
            return
        self.env['website']._alokai_trigger_dirty_cron()

    def write(self, vals):
        res = super(StockQuant, self).write(vals)
        quantity_fields = {'quantity', 'reserved_quantity', 'inventory_quantity'}
        if quantity_fields.intersection(vals.keys()):
            self._create_stock_is_dirty_redis()
            # Run the sync now rather than on the next minute's tick.
            self._trigger_dirty_stock_cron()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        quants = super(StockQuant, self).create(vals_list)
        quants._create_stock_is_dirty_redis()
        quants._trigger_dirty_stock_cron()
        return quants

    def unlink(self):
        self._create_stock_is_dirty_redis()
        self._trigger_dirty_stock_cron()
        return super(StockQuant, self).unlink()
