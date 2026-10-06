# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# Odoo stores the content, it does not understand it. Block definitions live in
# the storefront, which is the only place that knows what a "hero" is or which
# fields it has. This model therefore validates STRUCTURE only - is it a list,
# is each entry an object with a block type, is it a sane size - and never
# field shapes. Schema validation happens in the storefront before the write
# ever reaches here.
#
# The consequence is that a new block type costs a Vue component and a schema
# entry, and no Odoo change at all.

MAX_BLOCKS = 200
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024

DEFAULT_REVISION_LIMIT = 10


def pretty_blocks(blocks):
    """Blocks as indented JSON, for reading in the backend."""
    if not blocks:
        return False
    return json.dumps(blocks, indent=2, ensure_ascii=False)


def validate_blocks_structure(blocks):
    """Cheap structural backstop. Raises ValidationError, returns nothing.

    Deliberately not a schema check: see the module docstring above.
    """
    # Odoo's Json field reads back False for an empty value, not None, so
    # check falsiness rather than identity. An empty page is valid - a merchant
    # who deletes every block has an empty page, not a broken one.
    if not blocks:
        return

    if not isinstance(blocks, list):
        raise ValidationError(_('Page content must be a list of blocks.'))

    if len(blocks) > MAX_BLOCKS:
        raise ValidationError(
            _('A page may not have more than %s blocks.') % MAX_BLOCKS
        )

    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValidationError(
                _('Block %s is not an object.') % index
            )

        block_type = block.get('blockType')
        if not isinstance(block_type, str) or not block_type.strip():
            raise ValidationError(
                _('Block %s has no block type.') % index
            )

        if not isinstance(block.get('data', {}), dict):
            raise ValidationError(
                _('Block %s has invalid data.') % index
            )

    size = len(json.dumps(blocks))
    if size > MAX_PAYLOAD_BYTES:
        raise ValidationError(
            _('Page content is too large (%s bytes).') % size
        )


class AlokaiPageRevision(models.Model):
    """An immutable snapshot of a page's published content.

    Every publish creates one. Restoring an old revision COPIES it forward into
    a new one rather than moving a pointer, which keeps history append-only -
    "what was live on 3 March?" always has an answer - and means the live
    revision is always the newest, so pruning never has to make an exception
    for it.

    Content is one JSON document per revision rather than a row per block.
    Publish is then a single insert and can never leave a page half-published,
    reading a live page is a single row with no join or ordering, and there is
    no sequence bookkeeping to get wrong.
    """

    _name = 'alokai.page.revision'
    _description = 'Alokai CMS Page Revision'
    _order = 'page_id, number desc'

    page_id = fields.Many2one(
        'alokai.website.page',
        string='Page',
        required=True,
        index=True,
        ondelete='cascade',
    )
    number = fields.Integer(string='Revision', required=True, readonly=True)
    blocks = fields.Json(string='Blocks', readonly=True)

    restored_from_id = fields.Many2one(
        'alokai.page.revision',
        string='Restored From',
        readonly=True,
        ondelete='set null',
        help='Set when this revision was created by restoring an older one. '
             'Goes null once the source revision is pruned.',
    )
    # The durable half of the audit trail. The m2o above is convenient but
    # fragile: restoring an old revision can itself prune the source, and a
    # merchant asking "where did this come from?" deserves an answer after
    # that. An integer survives.
    restored_from_number = fields.Integer(
        string='Restored From Revision', readonly=True,
    )

    # Mirrored by the storefront on publish. `blocks` is opaque to Odoo, so
    # anything Odoo must answer a question about has to live outside it. These
    # exist for exactly one question - "which live pages reference product X?"
    # - which is how a product change invalidates the right pages. Without
    # them, cache invalidation falls back to TTL staleness.
    product_tmpl_ids = fields.Many2many(
        'product.template',
        'alokai_page_revision_product_template_rel',
        'revision_id',
        'product_tmpl_id',
        string='Referenced Products',
    )
    category_ids = fields.Many2many(
        'product.public.category',
        'alokai_page_revision_category_rel',
        'revision_id',
        'category_id',
        string='Referenced Categories',
    )
    # Also keeps images reachable by the ORM. An attachment referenced only
    # from inside opaque JSON looks like an orphan to Odoo.
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'alokai_page_revision_attachment_rel',
        'revision_id',
        'attachment_id',
        string='Referenced Images',
    )

    # For the debug-mode backend views only: the web client has no widget
    # for Json fields, so the blocks are shown as indented text.
    blocks_display = fields.Text(
        string='Blocks (JSON)', compute='_compute_blocks_display')

    @api.depends('blocks')
    def _compute_blocks_display(self):
        for revision in self:
            revision.blocks_display = pretty_blocks(revision.blocks)

    _page_number_uniq = models.Constraint(
        'UNIQUE (page_id, number)',
        'A page cannot have two revisions with the same number.',
    )

    def init(self):
        super().init()
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS alokai_page_revision_page_number_idx
            ON alokai_page_revision(page_id, number DESC);
        """)

    def write(self, vals):
        # Immutability is the property the whole design rests on: it is what
        # makes "what was live on 3 March?" answerable and revisions safe to
        # cache. The mirrored reference columns are the exception - they are
        # written once immediately after create, in the same transaction.
        mutable = {'product_tmpl_ids', 'category_ids', 'attachment_ids'}
        if set(vals) - mutable:
            raise UserError(_(
                'Page revisions cannot be edited. Restore this revision to '
                'bring its content back, which creates a new revision.'
            ))
        return super().write(vals)

    @api.model
    def _revision_limit(self):
        """How many revisions to keep per page.

        A config parameter rather than a constant: a merchant with compliance
        needs will want more, one with thousands of pages will want fewer.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            'alokai_cms_revision_limit', DEFAULT_REVISION_LIMIT
        )
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_REVISION_LIMIT

        return limit if limit > 0 else DEFAULT_REVISION_LIMIT

    def _prune(self, page):
        """Drop revisions beyond the limit, newest kept.

        No exception is needed for the live revision: restore copies forward,
        so the live one is always the newest.
        """
        limit = self._revision_limit()
        revisions = self.search([('page_id', '=', page.id)], order='number desc')

        stale = revisions[limit:]
        if stale:
            stale.unlink()
