# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import models, api, fields, tools, _


class PaymentTransactionInherit(models.Model):
    _inherit = 'payment.transaction'

    stripe_payment_risk_level = fields.Char(string='Risk Level')
    stripe_payment_risk_score = fields.Char(string='Risk Score')
    stripe_payment_risk_reason = fields.Char(string='Risk Reason')

    def _stripe_prepare_payment_intent_payload(self):
        payment_intent_payload = super()._stripe_prepare_payment_intent_payload()
        payment_intent_payload['payment_method_types[]'] = self.provider_id.payment_method_ids.mapped('code')
        return payment_intent_payload
