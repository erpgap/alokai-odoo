# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging
import pprint
import werkzeug

from werkzeug import urls

from odoo import http, _
from odoo.http import request
from odoo.exceptions import ValidationError
from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_adyen import utils as adyen_utils
from odoo.addons.payment_adyen.controllers.main import AdyenController
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing

_logger = logging.getLogger(__name__)


class AdyenControllerInherit(AdyenController):

    _webhook_url = AdyenController()._webhook_url

    @http.route()
    def adyen_payments(
        self, provider_id, reference, converted_amount, currency_id, partner_id, payment_method,
        access_token, browser_info=None
    ):
        """ overwrite to inject real ip as shopper ip
        """
        # Check that the transaction details have not been altered. This allows preventing users
        # from validating transactions by paying less than agreed upon.
        if not payment_utils.check_access_token(
            access_token, reference, converted_amount, currency_id, partner_id
        ):
            raise ValidationError("Adyen: " + _("Received tampered payment request data."))

        # Prepare the payment request to Adyen
        provider_sudo = request.env['payment.provider'].sudo().browse(provider_id).exists()
        tx_sudo = request.env['payment.transaction'].sudo().search([('reference', '=', reference)])

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
            'additionalData': {
                'authenticationData.threeDSRequestData.nativeThreeDS': True,
            },
            'channel': 'web',  # Required to support 3DS
            'origin': provider_sudo.get_base_url(),  # Required to support 3DS
            'browserInfo': browser_info,  # Required to support 3DS
            'returnUrl': urls.url_join(
                provider_sudo.get_base_url(),
                # Include the reference in the return url to be able to match it after redirection.
                # The key 'merchantReference' is chosen on purpose to be the same as that returned
                # by the /payments endpoint of Adyen.
                f'/payment/adyen/return?merchantReference={reference}'
            ),
            **adyen_utils.include_partner_addresses(tx_sudo),
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

        # Make the payment request to Adyen
        idempotency_key = payment_utils.generate_idempotency_key(
            tx_sudo, scope='payment_request_controller'
        )
        response_content = provider_sudo._adyen_make_request(
            endpoint='/payments', payload=data, method='POST', idempotency_key=idempotency_key
        )

        # Handle the payment request response
        _logger.info(
            "payment request response for transaction with reference %s:\n%s",
            reference, pprint.pformat(response_content)
        )
        tx_sudo._handle_notification_data(
            'adyen', dict(response_content, merchantReference=reference),  # Match the transaction
        )
        return response_content

    @http.route()
    def adyen_return_from_3ds_auth(self, **data):
        """ overwrite to redirect to alokai if transaction is created from alokai
        """
        # Retrieve the transaction based on the reference included in the return url
        tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
            'adyen', data
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
        # Redirect to Alokai
        alokai_payment_success_return_url = website.alokai_payment_success_return_url
        alokai_payment_error_return_url = website.alokai_payment_error_return_url

        request.session["__payment_monitored_tx_id__"] = tx_sudo.id

        # Retrieve the transaction based on the reference included in the return url
        tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
            'adyen', data
        )

        # Overwrite the operation to force the flow to 'redirect'. This is necessary because even
        # thought Adyen is implemented as a direct payment provider, it will redirect the user out
        # of Odoo in some cases. For instance, when a 3DS1 authentication is required, or for
        # special payment methods that are not handled by the drop-in (e.g. Sofort).
        tx_sudo.operation = 'online_redirect'

        # Query and process the result of the additional actions that have been performed
        _logger.info(
            "handling redirection from Adyen for transaction with reference %s with data:\n%s",
            tx_sudo.reference, pprint.pformat(data)
        )
        result = self.adyen_payment_details(
            tx_sudo.provider_id.id,
            data['merchantReference'],
            {
                'details': {
                    'redirectResult': data['redirectResult'],
                },
            },
        )

        if tx_sudo.created_on_alokai:
            # For Redirect 3DS2 and MobilePay (Success flow)
            if result and result.get('resultCode') and result['resultCode'] == 'Authorised':

                # Confirm sale order
                PaymentPostProcessing().poll_status()

                return werkzeug.utils.redirect(alokai_payment_success_return_url)

            # For Redirect 3DS2 and MobilePay (Cancel/Error flow)
            elif result and result.get('resultCode') and result['resultCode'] in ['Refused', 'Cancelled']:

                return werkzeug.utils.redirect(alokai_payment_error_return_url)

        else:
            # Redirect the user to the status page
            return request.redirect('/payment/status')
    
    @http.route()
    def adyen_webhook(self):
        """ overwrite to redirect to alokai if transaction is created from alokai
        """
        data = request.get_json_data()
        for notification_item in data['notificationItems']:
            notification_data = notification_item['NotificationRequestItem']

            _logger.info(
                "notification received from Adyen with data:\n%s", pprint.pformat(notification_data)
            )
            try:
                # Check the integrity of the notification
                tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                    'adyen', notification_data
                )
            except ValidationError:
                # Warn rather than log the traceback to avoid noise when a POS payment notification
                # is received and the corresponding `payment.transaction` record is not found.
                _logger.warning("unable to find the transaction; skipping to acknowledge")
            else:
                self._verify_notification_signature(notification_data, tx_sudo)

                # Check whether the event of the notification succeeded and reshape the notification
                # data for parsing
                success = notification_data['success'] == 'true'
                event_code = notification_data['eventCode']
                if event_code == 'AUTHORISATION' and success:
                    notification_data['resultCode'] = 'Authorised'
                elif event_code == 'CANCELLATION':
                    notification_data['resultCode'] = 'Cancelled' if success else 'Error'
                elif event_code in ['REFUND', 'CAPTURE']:
                    notification_data['resultCode'] = 'Authorised' if success else 'Error'
                elif event_code == 'CAPTURE_FAILED' and success:
                    # The capture failed after a capture notification with success = True was sent
                    notification_data['resultCode'] = 'Error'
                else:
                    continue  # Don't handle unsupported event codes and failed events
                try:
                    # Handle the notification data as if they were feedback of a S2S payment request
                    tx_sudo._handle_notification_data('adyen', notification_data)

                    if event_code == 'AUTHORISATION' and success and tx_sudo.created_on_alokai:
                        # Check the Order and respective website related with the transaction
                        # Check the payment_return url for the success and error pages
                        sale_order_ids = tx_sudo.sale_order_ids.ids
                        sale_order = request.env['sale.order'].sudo().search([
                            ('id', 'in', sale_order_ids), ('website_id', '!=', False)
                        ], limit=1)

                        # Get Website
                        website = sale_order.website_id
                        # Redirect to Alokai
                        alokai_payment_success_return_url = website.alokai_payment_success_return_url

                        request.session["__payment_monitored_tx_id__"] = tx_sudo.id

                        # Confirm sale order
                        PaymentPostProcessing().poll_status()

                        return werkzeug.utils.redirect(alokai_payment_success_return_url)
                except ValidationError:  # Acknowledge the notification to avoid getting spammed
                    _logger.exception(
                        "unable to handle the notification data;skipping to acknowledge"
                    )

        return request.make_json_response('[accepted]')  # Acknowledge the notification