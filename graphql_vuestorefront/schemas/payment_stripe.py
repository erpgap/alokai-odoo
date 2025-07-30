# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphene.types import generic
from graphql import GraphQLError
import json

from odoo import _

from odoo.addons.payment import utils as payment_utils
from odoo.addons.website_sale.controllers.main import PaymentPortal
from odoo.addons.payment_stripe_vsf.controllers.main import StripeControllerInherit
from odoo.addons.payment_stripe.const import API_VERSION, PROXY_URL

# --------------------------------- #
#           Stripe Payment          #
# --------------------------------- #

class StripeProviderInfoResult(graphene.ObjectType):
    stripe_provider_info = generic.GenericScalar()


class StripeTransactionResult(graphene.ObjectType):
    transaction = generic.GenericScalar()


class StripeGetInlineFormValuesResult(graphene.ObjectType):
    stripe_get_inline_form_values = generic.GenericScalar()


class StripeApplepayGetShippingOptionsResult(graphene.ObjectType):
    stripe_applepay_get_shipping_options = generic.GenericScalar()


class StripeApplepaySelectShippingMethodResult(graphene.ObjectType):
    stripe_applepay_select_shipping_method = generic.GenericScalar()


class StripeProviderInfo(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)

    Output = StripeProviderInfoResult

    @staticmethod
    def mutate(self, info, provider_id):
        env = info.context["env"]
        PaymentProvider = env['payment.provider'].sudo()
        domain = [
            ('id', '=', provider_id),
            ('state', 'in', ['enabled', 'test']),
        ]

        payment_provider = PaymentProvider.search(domain, limit=1)
        if not payment_provider:
            raise GraphQLError(_('Payment Provider does not exist.'))

        if not payment_provider.code == 'stripe':
            raise GraphQLError(_('Payment Provider "Stripe" does not exist.'))

        stripe_provider_info = {
            'state': payment_provider.state,
            'publishable_key': payment_provider.stripe_publishable_key,
            'api_version': API_VERSION,
            'proxy_url': PROXY_URL
        }

        return StripeProviderInfoResult(stripe_provider_info=stripe_provider_info)


class StripeGetInlineFormValues(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)

    Output = StripeGetInlineFormValuesResult

    @staticmethod
    def mutate(self, info, provider_id):
        env = info.context["env"]
        PaymentProvider = env['payment.provider'].sudo()
        website = env['website'].get_current_website()
        order = website.sale_get_order()
        domain = [
            ('id', '=', provider_id),
            ('state', 'in', ['enabled', 'test']),
        ]

        payment_provider = PaymentProvider.search(domain, limit=1)
        if not payment_provider:
            raise GraphQLError(_('Payment Provider does not exist.'))

        if not payment_provider.code == 'stripe':
            raise GraphQLError(_('Payment Provider "Stripe" does not exist.'))

        stripe_get_inline_form_values = payment_provider._stripe_get_inline_form_values(
            amount=order.amount_total,
            currency=order.currency_id,
            partner_id=order.partner_invoice_id.id,
            is_validation=True,
            sale_order_id=order.id
        )
        stripe_get_inline_form_values = json.loads(stripe_get_inline_form_values)
        stripe_get_inline_form_values['payment_methods'] = payment_provider.payment_method_ids.mapped('code')
        # Condition to prevent calling the "Affirm" payment_method when the amount is less than 50.00$
        if float(order.amount_total) < 50.00:
            if 'affirm' in stripe_get_inline_form_values['payment_methods']:
                stripe_get_inline_form_values['payment_methods'].remove('affirm')
        return StripeGetInlineFormValuesResult(stripe_get_inline_form_values=stripe_get_inline_form_values)


class StripeTransaction(graphene.Mutation):
    class Arguments:
        provider_id = graphene.Int(required=True)
        tokenization_requested = graphene.Boolean(default_value=False)

    Output = StripeTransactionResult

    @staticmethod
    def mutate(self, info, provider_id, tokenization_requested):
        env = info.context["env"]
        PaymentProvider = env['payment.provider'].sudo()
        PaymentTransaction = env['payment.transaction'].sudo()
        website = env['website'].get_current_website()
        order = website.sale_get_order()
        domain = [
            ('id', '=', provider_id),
            ('state', 'in', ['enabled', 'test']),
        ]

        payment_provider = PaymentProvider.search(domain, limit=1)
        payment_method = payment_provider.payment_method_ids[0] if payment_provider.payment_method_ids else None

        if not payment_method:
            raise GraphQLError(_('Payment Method does not exist.'))

        if not payment_provider:
            raise GraphQLError(_('Payment Provider does not exist.'))

        if not payment_provider.code == 'stripe':
            raise GraphQLError(_('Payment Provider "Stripe" does not exist.'))

        # Generate a new access token
        access_token = payment_utils.generate_access_token(order.partner_invoice_id.id, order.amount_total, order.currency_id.id)
        order.access_token = access_token

        transaction = PaymentPortal().shop_payment_transaction(
            order_id=order.id,
            access_token=order.access_token,
            provider_id=provider_id,
            payment_method_id=payment_method.id,
            token_id=None,
            amount=order.amount_total,
            flow='direct',
            tokenization_requested=tokenization_requested,
            landing_route='/shop/payment/validate',
        )

        transaction_id = PaymentTransaction.search([('reference', '=', transaction['reference'])], limit=1)

        # Update the field created_on_vsf
        transaction_id.created_on_vsf = True

        return StripeTransactionResult(transaction=transaction)


class StripeApplepayGetShippingOptions(graphene.Mutation):
    class Arguments:
        transaction_reference = graphene.String(required=True)

    Output = StripeApplepayGetShippingOptionsResult

    @staticmethod
    def mutate(self, info, transaction_reference):
        env = info.context["env"]
        PaymentTransaction = env['payment.transaction'].sudo()
        transaction = PaymentTransaction.search([('reference', '=', transaction_reference)], limit=1)

        if not transaction:
            raise GraphQLError(_('Payment Transaction does not exist.'))
        stripe_applepay_get_shipping_options = StripeControllerInherit().stripe_applepay_get_shipping_options(
            transaction_reference=transaction_reference
        )
        return StripeApplepayGetShippingOptionsResult(stripe_applepay_get_shipping_options=stripe_applepay_get_shipping_options)


class StripeApplepaySelectShippingMethod(graphene.Mutation):
    class Arguments:
        transaction_reference = graphene.String(required=True)
        carrier_id = graphene.Int(required=True)

    Output = StripeApplepaySelectShippingMethodResult

    @staticmethod
    def mutate(self, info, transaction_reference, carrier_id):
        env = info.context["env"]
        PaymentTransaction = env['payment.transaction'].sudo()
        transaction = PaymentTransaction.search([('reference', '=', transaction_reference)], limit=1)

        if not transaction:
            raise GraphQLError(_('Payment Transaction does not exist.'))
        stripe_applepay_select_shipping_method = StripeControllerInherit().stripe_applepay_select_shipping_method(
            transaction_reference=transaction_reference,
            carrier_id=carrier_id
        )
        return StripeApplepaySelectShippingMethodResult(stripe_applepay_select_shipping_method=stripe_applepay_select_shipping_method)


class StripePaymentMutation(graphene.ObjectType):
    stripe_provider_info = StripeProviderInfo.Field(description='Get Stripe Provider Info.')
    stripe_get_inline_form_values = StripeGetInlineFormValues.Field(description='Get Stripe Inline Form Values')
    stripe_transaction = StripeTransaction.Field(description='Create Stripe Transaction')
    stripe_applepay_get_shipping_options = StripeApplepayGetShippingOptions.Field(description='Get Shipping Options on "Stripe - ApplePay"')
    stripe_applepay_select_shipping_method = StripeApplepaySelectShippingMethod.Field(description='Select Shipping Method on "Stripe - ApplePay"')
