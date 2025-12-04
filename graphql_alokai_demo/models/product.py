# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo.fields import Domain
from odoo import models, fields, api, _
from odoo.addons.mail.tools.discuss import Store


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    review_ids = fields.One2many(compute = '_get_review_ids', comodel_name='mail.message', string='Reviews')

    def _get_review_ids(self):
        # Get the review ids from the mail.message model
        # and set them to the review_ids field
        for record in self:
            domain = Domain.AND([
                [('rating_value', '>', 0)],
                [('model', '=', 'product.template')],
                [('res_id', '=', record.id), '|', ('body', '!=', ''), ('attachment_ids', '!=', False),
                 ("subtype_id", "=", self.env.ref("mail.mt_comment").id)]
            ])
            record.review_ids = self.env['mail.message'].search(domain)
