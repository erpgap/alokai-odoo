# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Stripe return-URL handling for storefront (Alokai) payments.

After the shopper authorises the payment, Stripe redirects the browser to
Odoo's Stripe return URL. For payments started from the storefront we process
the result here (the same idempotent step the webhook uses) and then redirect
the browser back to the storefront's success or error page. Payments that did
not originate from the storefront keep Odoo's default behaviour.

The webhook remains the authoritative confirmation channel; this redirect is a
fast path for the shopper and is safe to lose.
"""

import logging

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.addons.payment_stripe.controllers.main import StripeController

_logger = logging.getLogger(__name__)

# Stripe PaymentIntent statuses that mean "payment taken or in progress" — the
# shopper should land on the success/confirmation page. Everything else (e.g.
# requires_payment_method, canceled) is treated as a failure.
_SUCCESSFUL_INTENT_STATUSES = ('succeeded', 'requires_capture', 'processing')


class StripeControllerAlokai(StripeController):

    @http.route()
    def stripe_return(self, **data):
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
            'stripe', data)

        # Not a storefront-originated payment: keep Odoo's default behaviour.
        if not tx_sudo or not tx_sudo.created_on_alokai:
            return super().stripe_return(**data)

        endpoint = (
            f'payment_intents/{data.get("payment_intent")}'
            if tx_sudo.operation != 'validation'
            else f'setup_intents/{data.get("setup_intent")}'
        )
        status = None
        try:
            response_content = tx_sudo._send_api_request(
                'GET', endpoint, data={'expand[]': 'payment_method'})
        except ValidationError:
            _logger.exception("Alokai Stripe: failed to fetch intent on return.")
        else:
            status = response_content.get('status')
            if tx_sudo.operation != 'validation':
                self._include_payment_intent_in_payment_data(response_content, data)
            else:
                self._include_setup_intent_in_payment_data(response_content, data)
            # Idempotent: confirms the order/invoice if not already done by the
            # webhook. A second call for an already-processed tx is a no-op.
            tx_sudo._process('stripe', data)

        # Let the storefront thank-you page read the outcome from the session.
        request.session['__payment_monitored_tx_id__'] = tx_sudo.id

        website = tx_sudo.sale_order_ids[:1].website_id
        success = status in _SUCCESSFUL_INTENT_STATUSES
        target = (
            website.alokai_payment_success_return_url if success
            else website.alokai_payment_error_return_url
        ) or '/payment/status'
        return request.redirect(target)
