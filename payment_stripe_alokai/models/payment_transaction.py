# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.addons.payment_stripe import const
from odoo.addons.payment_stripe.controllers.main import StripeController

_logger = logging.getLogger(__name__)

# A storefront payment that is still unconfirmed in Odoo this long after it was
# opened is re-checked against Stripe. Older than the upper bound is treated as
# abandoned and no longer polled.
_RECONCILE_MIN_AGE = timedelta(minutes=10)
_RECONCILE_MAX_AGE = timedelta(days=2)
_RECONCILED_INTENT_STATUSES = ('succeeded', 'requires_capture')


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _stripe_prepare_payment_intent_payload(self):
        """Let storefront PaymentIntents accept every enabled Stripe method.

        The storefront renders a single Stripe Payment Element offering all of
        the provider's methods, so the PaymentIntent must allow them all. Odoo's
        own (backend) Stripe payments keep the default single-method payload.
        """
        payload = super()._stripe_prepare_payment_intent_payload()
        if self.created_on_alokai:
            payload['payment_method_types[]'] = [
                const.PAYMENT_METHODS_MAPPING.get(code, code)
                for code in self.provider_id.payment_method_ids.mapped('code')
            ]
        return payload

    @api.model
    def _cron_alokai_stripe_reconcile(self):
        """Safety net: confirm storefront Stripe payments lost in transit.

        Re-checks, against Stripe, any storefront payment that is still
        unconfirmed in Odoo but may have succeeded at Stripe (webhook and return
        redirect both failed to arrive), and processes it through the same
        idempotent step the webhook uses.
        """
        now = fields.Datetime.now()
        transactions = self.sudo().search([
            ('provider_code', '=', 'stripe'),
            ('created_on_alokai', '=', True),
            ('state', 'in', ('draft', 'pending')),
            ('provider_reference', '!=', False),
            ('create_date', '<', now - _RECONCILE_MIN_AGE),
            ('create_date', '>', now - _RECONCILE_MAX_AGE),
        ])
        for tx in transactions:
            try:
                with self.env.cr.savepoint():
                    intent = tx._send_api_request(
                        'GET',
                        f'payment_intents/{tx.provider_reference}',
                        data={'expand[]': 'payment_method'},
                    )
                    if intent.get('status') in _RECONCILED_INTENT_STATUSES:
                        data = {'reference': tx.reference}
                        StripeController._include_payment_intent_in_payment_data(intent, data)
                        tx._process('stripe', data)
                        _logger.info(
                            "Alokai Stripe reconcile: recovered payment for tx %s.",
                            tx.reference,
                        )
            except Exception:  # noqa: BLE001 - never let one tx block the rest
                _logger.exception(
                    "Alokai Stripe reconcile failed for tx %s.", tx.reference)
