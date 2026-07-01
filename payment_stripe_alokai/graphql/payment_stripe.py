# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""GraphQL operations the Alokai storefront uses to pay with Stripe.

Two steps mirror Odoo's own Stripe inline-payment flow:

* ``stripeGetInlineFormValues`` returns the data to render the Stripe payment
  widget (publishable key, amount/currency from the Odoo cart, methods).
* ``stripeTransaction`` opens an Odoo payment for the cart, has Odoo create the
  Stripe PaymentIntent, and returns the one-time client secret the storefront
  uses to confirm the payment with Stripe.

Neither operation confirms the order: confirmation only happens when Odoo hears
the result from Stripe (webhook / return / reconciliation).
"""

import json

import graphene
from graphene.types import generic
from graphql import GraphQLError

from odoo import _
from odoo.addons.payment_stripe import const
from odoo.addons.website_sale.controllers.payment import PaymentPortal
from odoo.addons.graphql_alokai.graphql.registry import mutation_registry


def _get_stripe_provider(env, provider_id):
    """Return an enabled/test Stripe provider or raise a GraphQL error."""
    provider = env['payment.provider'].sudo().search([
        ('id', '=', provider_id),
        ('code', '=', 'stripe'),
        ('state', 'in', ['enabled', 'test']),
    ], limit=1)
    if not provider:
        raise GraphQLError(_('Stripe payment provider does not exist.'))
    return provider


def _get_cart(env):
    """Return the current storefront cart or raise a GraphQL error."""
    website = env['website'].get_current_website()
    order = website._get_and_cache_current_cart()
    if not order:
        raise GraphQLError(_('Shopping cart not found.'))
    return order


class StripeGetInlineFormValuesResult(graphene.ObjectType):
    stripe_get_inline_form_values = generic.GenericScalar()


class StripeTransactionResult(graphene.ObjectType):
    transaction = generic.GenericScalar()


class StripeGetInlineFormValues(graphene.Mutation):
    """Return the values needed to render the Stripe payment widget."""

    class Arguments:
        provider_id = graphene.Int(required=True)

    Output = StripeGetInlineFormValuesResult

    @staticmethod
    def mutate(self, info, provider_id):
        env = info.context['env']
        provider = _get_stripe_provider(env, provider_id)
        order = _get_cart(env)

        payment_method = provider.payment_method_ids[:1]
        if not payment_method:
            raise GraphQLError(_('No payment method configured for Stripe.'))

        values = json.loads(provider._stripe_get_inline_form_values(
            amount=order.amount_total,
            currency=order.currency_id,
            partner_id=order.partner_id.id,
            is_validation=False,
            payment_method_sudo=payment_method,
        ))

        # The Stripe Payment Element shows every enabled method of the provider;
        # expose them (mapped to Stripe's codes) plus the pinned API version.
        values['payment_methods'] = list(dict.fromkeys(
            const.PAYMENT_METHODS_MAPPING.get(code, code)
            for code in provider.payment_method_ids.mapped('code')
        ))
        values['api_version'] = const.API_VERSION
        values['provider_state'] = provider.state

        return StripeGetInlineFormValuesResult(stripe_get_inline_form_values=values)


class StripeTransaction(graphene.Mutation):
    """Open an Odoo payment for the cart and return Stripe processing values.

    Creating the transaction makes Odoo create the Stripe PaymentIntent and
    return its one-time ``client_secret`` and ``return_url``. The PaymentIntent
    id is stored on the transaction so the reconciliation job can recover the
    payment if neither the webhook nor the return redirect reaches Odoo.
    """

    class Arguments:
        provider_id = graphene.Int(required=True)
        tokenization_requested = graphene.Boolean(default_value=False)

    Output = StripeTransactionResult

    @staticmethod
    def mutate(self, info, provider_id, tokenization_requested):
        env = info.context['env']
        provider = _get_stripe_provider(env, provider_id)
        order = _get_cart(env)

        payment_method = provider.payment_method_ids[:1]
        if not payment_method:
            raise GraphQLError(_('No payment method configured for Stripe.'))

        # Use the order's own portal access token; shop_payment_transaction
        # binds the charge amount to the order server-side and locks the order
        # row to prevent concurrent payments.
        access_token = order.sudo()._portal_ensure_token()

        processing_values = PaymentPortal().shop_payment_transaction(
            order_id=order.id,
            access_token=access_token,
            provider_id=provider.id,
            payment_method_id=payment_method.id,
            token_id=None,
            amount=order.amount_total,
            flow='direct',
            tokenization_requested=tokenization_requested,
            landing_route='/payment/status',
        )

        reference = processing_values.get('reference')
        client_secret = processing_values.get('client_secret') or ''
        tx = env['payment.transaction'].sudo().search(
            [('reference', '=', reference)], limit=1)
        if tx:
            vals = {'created_on_alokai': True}
            # The PaymentIntent id is the part of the client secret before
            # "_secret_"; storing it lets the reconciliation job poll Stripe.
            if '_secret_' in client_secret:
                vals['provider_reference'] = client_secret.split('_secret_')[0]
            tx.write(vals)

        return StripeTransactionResult(transaction=processing_values)


class StripePaymentMutation(graphene.ObjectType):
    stripe_get_inline_form_values = StripeGetInlineFormValues.Field(
        description='Values to render the Stripe payment widget.')
    stripe_transaction = StripeTransaction.Field(
        description='Open an Odoo payment and return Stripe processing values.')


mutation_registry.append(StripePaymentMutation)
