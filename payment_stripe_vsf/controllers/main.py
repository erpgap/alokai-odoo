# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging
import pprint
import werkzeug

from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.payment_stripe.const import HANDLED_WEBHOOK_EVENTS
from odoo.addons.payment_stripe.controllers.main import StripeController
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.website_sale.controllers.delivery import WebsiteSaleDelivery

_logger = logging.getLogger(__name__)


class StripeControllerInherit(StripeController):
    _return_url = StripeController()._return_url
    _webhook_url = StripeController()._webhook_url
    _apple_pay_domain_association_url = StripeController()._apple_pay_domain_association_url
    WEBHOOK_AGE_TOLERANCE = StripeController().WEBHOOK_AGE_TOLERANCE

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def stripe_return(self, **data):
        """ Process the notification data sent by Stripe after redirection from payment.

        Customers go through this route regardless of whether the payment was direct or with
        redirection to Stripe or to an external service (e.g., for strong authentication).

        :param dict data: The notification data, including the reference appended to the URL in
                          `_get_specific_processing_values`.
        """
        # Retrieve the transaction based on the reference included in the return url.
        tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
            'stripe', data
        )

        # Check the Order and respective website related with the transaction
        # Check the payment_return url for the success and error pages
        # Pass the transaction_id on the session
        sale_order_ids = tx_sudo.sale_order_ids.ids
        sale_order = request.env['sale.order'].sudo().search([
            ('id', 'in', sale_order_ids), ('website_id', '!=', False)
        ], limit=1)

        # Get Website
        website = sale_order.website_id
        # Redirect to VSF
        vsf_payment_success_return_url = website.vsf_payment_success_return_url
        vsf_payment_error_return_url = website.vsf_payment_error_return_url

        request.session["__payment_monitored_tx_id__"] = tx_sudo.id

        payment_intent = {}
        if tx_sudo.operation != 'validation':
            # Fetch the PaymentIntent and PaymentMethod objects from Stripe.
            payment_intent = tx_sudo.provider_id._stripe_make_request(
                f'payment_intents/{data.get("payment_intent")}',
                payload={'expand[]': 'payment_method'},  # Expand all required objects.
                method='GET',
            )
            # Populate the fields related to the "Payment Risk"
            if payment_intent.get('charges'):
                charges = payment_intent['charges']
                if charges.get('data'):
                    charges_data = charges['data']
                    if charges_data[0].get('outcome'):
                        charges_data_outcome = charges_data[0]['outcome']
                        tx_sudo.write({
                            'stripe_payment_risk_level': charges_data_outcome['risk_level'] if charges_data_outcome.get('risk_level') else False,
                            'stripe_payment_risk_score': charges_data_outcome['risk_score'] if charges_data_outcome.get('risk_score') else False,
                            'stripe_payment_risk_reason': charges_data_outcome['reason'] if charges_data_outcome.get('reason') else False,
                        })
            _logger.info("Received payment_intents response:\n%s", pprint.pformat(payment_intent))
            self._include_payment_intent_in_notification_data(payment_intent, data)
        else:
            # Fetch the SetupIntent and PaymentMethod objects from Stripe.
            setup_intent = tx_sudo.provider_id._stripe_make_request(
                f'setup_intents/{data.get("setup_intent")}',
                payload={'expand[]': 'payment_method'},  # Expand all required objects.
                method='GET',
            )
            _logger.info("Received setup_intents response:\n%s", pprint.pformat(setup_intent))
            self._include_setup_intent_in_notification_data(setup_intent, data)

        # Handle the notification data crafted with Stripe API's objects.
        tx_sudo._handle_notification_data('stripe', data)

        # Condition used for VSF
        if tx_sudo.created_on_vsf:
            if payment_intent:
                if payment_intent.get('status') and payment_intent['status'] in ['succeeded', 'requires_capture']:
                    # Confirm sale order
                    # PaymentPostProcessing().poll_status()
                    return werkzeug.utils.redirect(vsf_payment_success_return_url)
                else:
                    return werkzeug.utils.redirect(vsf_payment_error_return_url)
        # Default Condition
        else:
            # Redirect the user to the status page.
            return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['POST'], auth='public', csrf=False)
    def stripe_webhook(self):
        """ Process the notification data sent by Stripe to the webhook.

        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        event = request.get_json_data()
        _logger.info("Notification received from Stripe with data:\n%s", pprint.pformat(event))
        try:
            if event['type'] in HANDLED_WEBHOOK_EVENTS:
                stripe_object = event['data']['object']  # {Payment,Setup}Intent, Charge, or Refund.

                # Check the integrity of the event.
                data = {
                    'reference': stripe_object.get('description'),
                    'event_type': event['type'],
                    'object_id': stripe_object['id'],
                }
                tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                    'stripe', data
                )
                self._verify_notification_signature(tx_sudo)

                # Handle the notification data.
                if event['type'].startswith('payment_intent'):  # Payment operation.
                    if tx_sudo.tokenize:
                        payment_method = tx_sudo.provider_id._stripe_make_request(
                            f'payment_methods/{stripe_object["payment_method"]}', method='GET'
                        )
                        _logger.info(
                            "Received payment_methods response:\n%s", pprint.pformat(payment_method)
                        )
                        stripe_object['payment_method'] = payment_method
                    self._include_payment_intent_in_notification_data(stripe_object, data)
                elif event['type'].startswith('setup_intent'):  # Validation operation.
                    # Fetch the missing PaymentMethod object.
                    payment_method = tx_sudo.provider_id._stripe_make_request(
                        f'payment_methods/{stripe_object["payment_method"]}', method='GET'
                    )
                    _logger.info(
                        "Received payment_methods response:\n%s", pprint.pformat(payment_method)
                    )
                    stripe_object['payment_method'] = payment_method
                    self._include_setup_intent_in_notification_data(stripe_object, data)
                elif event['type'] == 'charge.refunded':  # Refund operation (refund creation).
                    refunds = stripe_object['refunds']['data']

                    # The refunds linked to this charge are paginated, fetch the remaining refunds.
                    has_more = stripe_object['refunds']['has_more']
                    while has_more:
                        payload = {
                            'charge': stripe_object['id'],
                            'starting_after': refunds[-1]['id'],
                            'limit': 100,
                        }
                        additional_refunds = tx_sudo.provider_id._stripe_make_request(
                            'refunds', payload=payload, method='GET'
                        )
                        refunds += additional_refunds['data']
                        has_more = additional_refunds['has_more']

                    # Process the refunds for which a refund transaction has not been created yet.
                    processed_refund_ids = tx_sudo.child_transaction_ids.filtered(
                        lambda tx: tx.operation == 'refund'
                    ).mapped('provider_reference')
                    for refund in filter(lambda r: r['id'] not in processed_refund_ids, refunds):
                        refund_tx_sudo = self._create_refund_tx_from_refund(tx_sudo, refund)
                        self._include_refund_in_notification_data(refund, data)
                        refund_tx_sudo._handle_notification_data('stripe', data)
                    # Don't handle the notification data for the source transaction.
                    return request.make_json_response('')
                elif event['type'] == 'charge.refund.updated':  # Refund operation (with update).
                    # A refund was updated by Stripe after it was already processed (possibly to
                    # cancel it). This can happen when the customer's payment method can no longer
                    # be topped up (card expired, account closed...). The `tx_sudo` record is the
                    # refund transaction to update.
                    self._include_refund_in_notification_data(stripe_object, data)

                # Handle the notification data crafted with Stripe API objects
                # Prevent this "Handle Notification" to prevent this error -> "Error, a partner cannot follow twice the same object."
                # tx_sudo._handle_notification_data('stripe', data)

        except ValidationError:  # Acknowledge the notification to avoid getting spammed
            _logger.exception("unable to handle the notification data; skipping to acknowledge")
        return request.make_json_response('')

    # ------------------------------- #
    #    ApplePay Express Checkout    #
    # ------------------------------- #

    @http.route('/stripe/applepay/shipping_methods', type='json', auth='public', csrf=False)
    def stripe_applepay_get_shipping_options(self, **post):
        data = post
        if not data:
            data = request.dispatcher.jsonrequest
        transaction_reference = data.get('transaction_reference')
        transaction = request.env['payment.transaction'].sudo().search([('reference', '=', transaction_reference)])

        order_ids = transaction.sale_order_ids.ids
        order = request.env['sale.order'].sudo().search([
            ('id', 'in', order_ids), ('website_id', '!=', False)
        ], limit=1)

        # Get Shipping Methods
        delivery_methods = order._get_delivery_methods()
        shipping_methods = []
        if delivery_methods:
            for delivery_method in delivery_methods:
                rate = WebsiteSaleDelivery._get_rate(delivery_method, order, is_express_checkout_flow=True)
                method = {
                    'id': str(delivery_method.id),
                    'label': delivery_method.name,
                    'detail': delivery_method.carrier_description if delivery_method.carrier_description else '',
                    'amount': rate['price'],
                }
                shipping_methods.append(method)

        new_total = {
            'label': order.name,
            'amount': round(order.amount_total, 2)
        }

        return {
            'shippingMethods': shipping_methods,
            'newTotal': new_total
        }

    @http.route('/stripe/applepay/select_shipping_method', type='json', auth='public',  csrf=False)
    def stripe_applepay_select_shipping_method(self, **post):
        data = post
        if not data:
            data = request.dispatcher.jsonrequest
        carrier_id = data.get('carrier_id')
        transaction_reference = data.get('transaction_reference')
        transaction = request.env['payment.transaction'].sudo().search([('reference', '=', transaction_reference)])

        order_ids = transaction.sale_order_ids.ids
        order = request.env['sale.order'].sudo().search([
            ('id', 'in', order_ids), ('website_id', '!=', False)
        ], limit=1)

        if order and carrier_id != order.carrier_id.id:
            order._check_carrier_quotation(force_carrier_id=int(carrier_id))

        # Calculate new total
        return {
            'newTotal': {
                'label': order.name,
                'amount': round(order.amount_total, 2)
            }
        }
