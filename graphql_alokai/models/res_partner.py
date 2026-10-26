# -*- coding: utf-8 -*-
from odoo import models, fields


class ResPartner(models.Model):
    _inherit = 'res.partner'

    user_ids = fields.One2many(context={'active_test': False})

    def _alokai_is_public(self):
        """Whether this is a website's public partner: the one an anonymous
        visitor's cart belongs to. Public users are archived, which is why
        user_ids above includes archived users."""
        self.ensure_one()
        return any(user._is_public() for user in self.user_ids)
