# -*- coding: utf-8 -*-
# Copyright 2025 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import models, api, fields, tools, _


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    stripe_payment_risk_level = fields.Char(string='Risk Level', compute='_compute_stripe_fields')
    stripe_payment_risk_score = fields.Char(string='Risk Score', compute='_compute_stripe_fields')
    stripe_payment_risk_reason = fields.Char(string='Risk Reason', compute='_compute_stripe_fields')

    @api.depends(
        'transaction_ids',
        'transaction_ids.state',
        'transaction_ids.provider_code'
    )
    def _compute_stripe_fields(self):
        for sale in self:
            stripe_payment_risk_level = False
            stripe_payment_risk_score = False
            stripe_payment_risk_reason = False
            if sale.transaction_ids:
                transaction_ids = sale.transaction_ids.filtered(lambda t: t.state in ('authorized', 'done') and t.provider_code == 'stripe').sorted('create_date', reverse=True)
                if transaction_ids:
                    stripe_payment_risk_level = transaction_ids[0].stripe_payment_risk_level
                    stripe_payment_risk_score = transaction_ids[0].stripe_payment_risk_score
                    stripe_payment_risk_reason = transaction_ids[0].stripe_payment_risk_reason
            sale.write({
                'stripe_payment_risk_level': stripe_payment_risk_level,
                'stripe_payment_risk_score': stripe_payment_risk_score,
                'stripe_payment_risk_reason': stripe_payment_risk_reason
            })
