# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""GraphQL operations the Alokai storefront uses to pay with Adyen.

Mirrors Odoo's Adyen drop-in flow:

* ``adyenProviderInfo`` / ``adyenPaymentMethods`` return what the storefront
  needs to render the Adyen web component (client key, available methods).
* ``adyenTransaction`` opens an Odoo payment for the cart and flags it as
  storefront-originated (``created_on_alokai``).
* ``adyenPayments`` / ``adyenPaymentDetails`` proxy the ``/payments`` and
  ``/payments/details`` requests (order confirmation happens through
  ``tx._process`` in the controller / webhook, not here).
"""

import graphene
from graphene.types import generic
from graphql import GraphQLError

from odoo import _
from odoo.tools import format_amount

from odoo.addons.payment import utils as payment_utils
from odoo.addons.website_sale.controllers.payment import PaymentPortal
from odoo.addons.payment_adyen.controllers.main import AdyenController
from odoo.addons.graphql_alokai.graphql.registry import mutation_registry

from odoo.addons.payment_adyen_alokai.const import CURRENCY_DECIMALS
from odoo.addons.payment_adyen_alokai.controllers.main import AdyenControllerInherit


def _get_adyen_provider(env, provider_id):
    """Return an enabled/test Adyen provider or raise a GraphQL error."""
    provider = env['payment.provider'].sudo().search([
        ('id', '=', provider_id),
        ('code', '=', 'adyen'),
        ('state', 'in', ['enabled', 'test']),
    ], limit=1)
    if not provider:
        raise GraphQLError(_('Adyen payment provider does not exist.'))
    return provider


def _get_cart(env):
    """Return the current storefront cart or raise a GraphQL error."""
    website = env['website'].get_current_website()
    order = website._get_and_cache_current_cart()
    if not order:
        raise GraphQLError(_('Shopping cart not found.'))
    return order


# -------------------------------- #
#           Adyen Payment          #
# -------------------------------- #

class AdyenProviderInfoResult(graphene.ObjectType):
    adyen_provider_info = generic.GenericScalar()


class AdyenPaymentMethodsResult(graphene.ObjectType):
    adyen_payment_methods = generic.GenericScalar()


class AdyenTransactionResult(graphene.ObjectType):
    transaction = generic.GenericScalar()


class AdyenPaymentsResult(graphene.ObjectType):
    adyen_payments = generic.GenericScalar()


class AdyenPaymentDetailsResult(graphene.ObjectType):
    adyen_payment_details = generic.GenericScalar()


class AdyenProviderInfo(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)

    Output = AdyenProviderInfoResult

    @staticmethod
    def mutate(self, info, provider_id):
        env = info.context["env"]
        provider = _get_adyen_provider(env, provider_id)

        adyen_provider_info = {
            'state': provider.state,
            'client_key': provider.adyen_client_key,
        }
        return AdyenProviderInfoResult(adyen_provider_info=adyen_provider_info)


class AdyenPaymentMethods(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)

    Output = AdyenPaymentMethodsResult

    @staticmethod
    def mutate(self, info, provider_id):
        env = info.context["env"]
        provider = _get_adyen_provider(env, provider_id)
        order = _get_cart(env)

        adyen_payment_methods = AdyenController().adyen_payment_methods(
            provider_id=provider.id,
            formatted_amount=format_amount(env, order.amount_total, order.currency_id),
            partner_id=order.partner_id.id,
        )
        return AdyenPaymentMethodsResult(adyen_payment_methods=adyen_payment_methods)


class AdyenTransaction(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)
        tokenization_requested = graphene.Boolean(default_value=False)

    Output = AdyenTransactionResult

    @staticmethod
    def mutate(self, info, provider_id, tokenization_requested):
        env = info.context["env"]
        PaymentTransaction = env['payment.transaction'].sudo()
        provider = _get_adyen_provider(env, provider_id)
        order = _get_cart(env)

        payment_method_id = provider.payment_method_ids[:1].id or None

        # Use the order's own portal access token; shop_payment_transaction binds the
        # charge amount to the order server-side and locks the order row.
        access_token = order.sudo()._portal_ensure_token()

        transaction = PaymentPortal().shop_payment_transaction(
            order_id=order.id,
            access_token=access_token,
            provider_id=provider.id,
            payment_method_id=payment_method_id,
            token_id=None,
            amount=order.amount_total,
            flow='direct',
            tokenization_requested=tokenization_requested,
            landing_route='/shop/payment/validate',
        )

        # Flag the transaction as storefront-originated so the controller overrides apply.
        tx = PaymentTransaction.search([('reference', '=', transaction['reference'])], limit=1)
        if tx:
            tx.created_on_alokai = True

        return AdyenTransactionResult(transaction=transaction)


class AdyenPayments(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)
        transaction_reference = graphene.String(required=True)
        access_token = graphene.String(required=True)
        payment_method = generic.GenericScalar(required=True, description='Return state.data.paymentMethod')
        browser_info = generic.GenericScalar(required=True, description='Return state.data.browserInfo')

    Output = AdyenPaymentsResult

    @staticmethod
    def mutate(self, info, provider_id, transaction_reference, access_token, payment_method, browser_info):
        env = info.context["env"]
        provider = _get_adyen_provider(env, provider_id)

        transaction = env['payment.transaction'].sudo().search(
            [('reference', '=', transaction_reference)], limit=1)
        if not transaction:
            raise GraphQLError(_('Payment transaction does not exist.'))

        converted_amount = payment_utils.to_minor_currency_units(
            transaction.amount,
            transaction.currency_id,
            arbitrary_decimal_number=CURRENCY_DECIMALS.get(transaction.currency_id.name, 2),
        )

        adyen_payment = AdyenControllerInherit().adyen_payments(
            provider_id=provider.id,
            reference=transaction.reference,
            converted_amount=converted_amount,
            currency_id=transaction.currency_id.id,
            partner_id=transaction.partner_id.id,
            payment_method=payment_method,
            access_token=access_token,
            browser_info=browser_info,
        )
        return AdyenPaymentsResult(adyen_payments=adyen_payment)


class AdyenPaymentDetails(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)
        transaction_reference = graphene.String(required=True)
        payment_details = generic.GenericScalar(required=True, description='Return state.data')

    Output = AdyenPaymentDetailsResult

    @staticmethod
    def mutate(self, info, provider_id, transaction_reference, payment_details):
        env = info.context["env"]
        provider = _get_adyen_provider(env, provider_id)

        transaction = env['payment.transaction'].sudo().search(
            [('reference', '=', transaction_reference)], limit=1)
        if not transaction:
            raise GraphQLError(_('Payment transaction does not exist.'))

        adyen_payment_details = AdyenController().adyen_payment_details(
            provider_id=provider.id,
            reference=transaction.reference,
            payment_details=payment_details,
        )
        return AdyenPaymentDetailsResult(adyen_payment_details=adyen_payment_details)


class AdyenPaymentMutation(graphene.ObjectType):
    adyen_provider_info = AdyenProviderInfo.Field(description='Get Adyen Provider Info.')
    adyen_payment_methods = AdyenPaymentMethods.Field(description='Get Adyen Payment Methods.')
    adyen_transaction = AdyenTransaction.Field(description='Create Adyen Transaction')
    adyen_payments = AdyenPayments.Field(description='Make Adyen Payment request.')
    adyen_payment_details = AdyenPaymentDetails.Field(description='Submit the Adyen Payment Details.')


mutation_registry.append(AdyenPaymentMutation)
