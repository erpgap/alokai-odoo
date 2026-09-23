# -*- coding: utf-8 -*-

from odoo import models, fields, api, _

from .alokai_page_revision import validate_blocks_structure


class AlokaiWebsitePage(models.Model):
    _name = 'alokai.website.page'
    _inherit = [
        'website.published.multi.mixin',
        'website.searchable.mixin',
    ]
    _description = 'Alokai Website Page'
    _order = 'website_id'

    def _default_content(self):
        return '<p class="o_default_snippet_text">' + _("Start writing here...") + '</p>'

    name = fields.Char(string='Page Name', translate=True, required=True)
    url = fields.Char(string='Page URL', translate=True)
    website_id = fields.Many2one('website', string="Website")
    date_publish = fields.Datetime('Publishing Date')
    content = fields.Text(string='Content', default=_default_content, translate=True)

    page_type = fields.Selection(selection=[
        ('static', 'Static Page'), ('products', 'Campaign Page')
    ], string='Page Type', default='static', required=True)

    product_tmpl_ids = fields.Many2many(
        'product.template', 'product_template_alokai_website_page_rel', 'alokai_page_id', 'product_tmpl_id',
        string='Product Templates'
    )

    # --- CMS -------------------------------------------------------------
    # Content is authored as blocks in the storefront studio. `content` and
    # `page_type` above are superseded by this and kept only so existing
    # installs keep working; they are removed in a later, separate migration.
    # `product_tmpl_ids` is likewise superseded by the mirrored references on
    # each revision.

    draft_blocks = fields.Json(
        string='Draft Content',
        help='What the editor is working on. Never served to visitors.',
    )
    draft_attachment_ids = fields.Many2many(
        'ir.attachment',
        'alokai_page_draft_attachment_rel',
        'page_id',
        'attachment_id',
        string='Draft Images',
        help='Keeps images used only by the draft reachable by the ORM.',
    )

    revision_ids = fields.One2many(
        'alokai.page.revision', 'page_id', string='Revisions',
    )
    live_revision_id = fields.Many2one(
        'alokai.page.revision',
        string='Live Revision',
        ondelete='restrict',
        copy=False,
        help='The revision visitors currently see.',
    )
    revision_count = fields.Integer(compute='_compute_revision_count')

    @api.depends('revision_ids')
    def _compute_revision_count(self):
        for page in self:
            page.revision_count = len(page.revision_ids)

    @api.constrains('draft_blocks')
    def _check_draft_blocks(self):
        # Structural backstop only. The storefront validates against the block
        # schema before it ever gets here - Odoo does not know what a block is.
        for page in self:
            validate_blocks_structure(page.draft_blocks)
