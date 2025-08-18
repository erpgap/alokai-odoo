# -*- coding: utf-8 -*-
from odoo import api, models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    @api.depends('user_ids.website_ids')
    def _compute_is_public_user(self):
        for partner in self:
            partner.is_public_user = any(user.website_ids for user in partner.user_ids)

    user_ids = fields.One2many(context={'active_test': False})
    is_public_user = fields.Boolean('Is Public User', compute='_compute_is_public_user', store=True, readonly=True)