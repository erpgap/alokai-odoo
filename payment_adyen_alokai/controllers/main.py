# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Adyen controller adjustments for storefront (Alokai) payments.

Three overrides on top of Odoo's core ``payment_adyen`` controller, each keeping
the core behaviour for non-storefront payments and only branching when the
transaction was ``created_on_alokai``:

* ``adyen_payments`` — same core v19 ``/payments`` request, but for storefront
  transactions the real shopper IP is taken from the ``HTTP_REAL_IP`` proxy
  header (Adyen needs the client IP, not the reverse-proxy IP, for risk/3DS).
* ``adyen_return_from_3ds_auth`` — after the 3DS/redirect result is processed
  (via ``tx._process``, which confirms the sale order), a storefront shopper is
  redirected back to the website's success/error page instead of Odoo's
  ``/payment/status``. The monitored transaction id is stored in the session so
  the storefront thank-you page can read the outcome (same as ``payment_stripe_alokai``).
* ``adyen_webhook`` — keeps ownership of the Adyen webhook route; the sale order
  is confirmed by the core ``tx._process`` call (no ``poll_status`` in the v19
  Alokai flow, matching ``payment_stripe_alokai``).
"""

import pprint

from odoo import _, http, release
from odoo.exceptions import ValidationError
from odoo.http import request
from odoo.tools import urls

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_adyen import utils as adyen_utils
from odoo.addons.payment_adyen.controllers.main import AdyenController

_logger = get_payment_logger(__name__)


class AdyenControllerInherit(AdyenController):

    @http.route()
    def adyen_payments(
        self, provider_id, reference, converted_amount, currency_id, partner_id, payment_method,
        access_token, browser_info=None
    ):
        """Override of the core ``/payments`` request to inject the real shopper IP.

        Behind a reverse proxy ``payment_utils.get_customer_ip_address()`` returns the
        proxy IP; for storefront (Alokai) transactions we prefer the real client IP from
        the ``HTTP_REAL_IP`` header. Everything else is the core v19 request.
        """
        # Check that the transaction details have not been altered. This allows preventing users
        # from validating transactions by paying less than agreed upon.
        if not payment_utils.check_access_token(
            access_token, reference, converted_amount, currency_id, partner_id
        ):
            raise ValidationError(_("Received tampered payment request data."))

        # Prepare the payment request to Adyen
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id).exists()
        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('provider_id', '=', provider_sudo.id), ('reference', '=', reference),
        ])
        partner_country_code = (
            tx_sudo.partner_country_id.code or provider_sudo.company_id.country_id.code or 'NL'
        )

        # --- Alokai adjustment: use the real shopper IP when behind a reverse proxy ---
        shopper_ip = payment_utils.get_customer_ip_address()
        if tx_sudo.created_on_alokai:
            if request.httprequest.headers.environ.get('HTTP_REAL_IP', False) and \
                    request.httprequest.headers.environ['HTTP_REAL_IP']:
                shopper_ip = request.httprequest.headers.environ['HTTP_REAL_IP']

        data = {
            'merchantAccount': provider_sudo.adyen_merchant_account,
            'amount': {
                'value': converted_amount,
                'currency': request.env['res.currency'].browse(currency_id).name,  # ISO 4217
            },
            'applicationInfo': {
                'externalPlatform': {
                    'name': 'Odoo',
                    'version': release.version,
                    'integrator': 'Odoo SA',
                }
            },
            'countryCode': partner_country_code,  # ISO 3166-1 alpha-2 (e.g.: 'BE')
            'reference': reference,
            'paymentMethod': payment_method,
            'shopperReference': provider_sudo._adyen_compute_shopper_reference(partner_id),
            'recurringProcessingModel': 'CardOnFile',  # Most susceptible to trigger a 3DS check
            'shopperIP': shopper_ip,
            'shopperInteraction': 'Ecommerce',
            'shopperEmail': tx_sudo.partner_email or "",
            'shopperName': adyen_utils.format_partner_name(tx_sudo.partner_name),
            'telephoneNumber': tx_sudo.partner_phone or "",
            'storePaymentMethod': tx_sudo.tokenize,  # True by default on Adyen side
            'authenticationData': {
                'threeDSRequestData': {
                    'nativeThreeDS': 'preferred',
                }
            },
            'channel': 'web',  # Required to support 3DS
            'origin': provider_sudo.get_base_url(),  # Required to support 3DS
            'browserInfo': browser_info,  # Required to support 3DS
            'returnUrl': urls.urljoin(
                provider_sudo.get_base_url(),
                # Include the reference in the return url to be able to match it after redirection.
                # The key 'merchantReference' is chosen on purpose to be the same as that returned
                # by the /payments endpoint of Adyen.
                f'/payment/adyen/return?merchantReference={reference}',
            ),
            **adyen_utils.include_partner_addresses(tx_sudo),
            'lineItems': [{
                'amountIncludingTax': converted_amount,
                'quantity': '1',
                'description': reference,
            }],
        }

        # Force the capture delay on Adyen side if the provider is not configured for capturing
        # payments manually. This is necessary because it's not possible to distinguish
        # 'AUTHORISATION' events sent by Adyen with the merchant account's capture delay set to
        # 'manual' from events with the capture delay set to 'immediate' or a number of hours. If
        # the merchant account is configured to capture payments with a delay but the provider is
        # not, we force the immediate capture to avoid considering authorized transactions as
        # captured on Odoo.
        if not provider_sudo.capture_manually:
            data.update(captureDelayHours=0)

        # Send the payment request to Adyen.
        idempotency_key = payment_utils.generate_idempotency_key(
            tx_sudo, scope='payment_request_controller'
        )
        response_content = provider_sudo._send_api_request(
            'POST', '/payments', json=data, idempotency_key=idempotency_key
        )
        tx_sudo._process(
            'adyen', dict(response_content, merchantReference=reference),  # Match the transaction
        )
        return response_content

    @http.route()
    def adyen_return_from_3ds_auth(self, **data):
        """Override to redirect storefront (Alokai) shoppers to the website success/error page.

        Non-storefront payments keep Odoo's default behaviour (process + redirect to
        ``/payment/status``).
        """
        # Retrieve the transaction based on the reference included in the return url.
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference('adyen', data)

        # Not a storefront-originated payment: keep Odoo's default behaviour.
        if not tx_sudo or not tx_sudo.created_on_alokai:
            return super().adyen_return_from_3ds_auth(**data)

        # Overwrite the operation to force the flow to 'redirect' (same as core). This is necessary
        # because even though Adyen is a direct payment provider, it redirects the user out of Odoo
        # in some cases (e.g. 3DS1, or methods not handled by the drop-in).
        tx_sudo.operation = 'online_redirect'

        _logger.info(
            "Handling Alokai redirection from Adyen for transaction %s with data:\n%s",
            tx_sudo.reference, pprint.pformat(data)
        )
        # Query and process the additional-action result (idempotent: same step the webhook uses).
        response_content = self.adyen_payment_details(
            tx_sudo.provider_id.id,
            data['merchantReference'],
            {
                'details': {
                    'redirectResult': data['redirectResult'],
                },
            },
        )

        # Let the storefront thank-you page read the outcome from the session.
        request.session['__payment_monitored_tx_id__'] = tx_sudo.id

        website = tx_sudo.sale_order_ids[:1].website_id
        success = bool(response_content) and response_content.get('resultCode') == 'Authorised'
        target = (
            website.alokai_payment_success_return_url if success
            else website.alokai_payment_error_return_url
        ) or '/payment/status'
        return request.redirect(target)

    @http.route()
    def adyen_webhook(self):
        """Keep the Adyen webhook route owned by the Alokai module.

        Same notification handling as core: the transaction (and its sale order)
        is confirmed by ``tx._process``. No ``poll_status`` is used in the v19
        Alokai flow, matching ``payment_stripe_alokai``.
        """
        data = request.get_json_data()
        for notification_item in data['notificationItems']:
            payment_data = notification_item['NotificationRequestItem']

            _logger.info(
                "notification received from Adyen with data:\n%s", pprint.pformat(payment_data)
            )
            # Check the integrity of the notification.
            tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
                'adyen', payment_data
            )
            if tx_sudo:
                self._verify_signature(payment_data, tx_sudo)

                # Check whether the event of the notification succeeded and reshape the notification
                # data for parsing.
                success = payment_data['success'] == 'true'
                event_code = payment_data['eventCode']
                if event_code == 'AUTHORISATION' and success:
                    payment_data['resultCode'] = 'Authorised'
                elif event_code == 'CANCELLATION':
                    payment_data['resultCode'] = 'Cancelled' if success else 'Error'
                elif event_code in ['REFUND', 'CAPTURE']:
                    payment_data['resultCode'] = 'Authorised' if success else 'Error'
                elif event_code == 'CAPTURE_FAILED' and success:
                    # The capture failed after a capture notification with success = True was sent.
                    payment_data['resultCode'] = 'Error'
                else:
                    continue  # Don't handle unsupported event codes and failed events.
                tx_sudo._process('adyen', payment_data)
        return request.make_json_response('[accepted]')  # Acknowledge the notification
