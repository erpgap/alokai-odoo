# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class VsfWebsitePage(models.Model):
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
