# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
from odoo import _
from odoo.http import request
from odoo.fields import Domain
from odoo.addons.graphql_alokai.graphql.registry import query_registry, mutation_registry

from odoo.addons.graphql_alokai.schemas.objects import PaymentProvider, PaymentTransaction
from odoo.addons.graphql_alokai.schemas.shop import Cart, CartData


class PaymentQuery(graphene.ObjectType):
    payment_provider = graphene.Field(
        PaymentProvider,
        required=True,
        id=graphene.Int(),
    )
    payment_providers = graphene.List(
        graphene.NonNull(PaymentProvider),
    )
    payment_transaction = graphene.Field(
        PaymentTransaction,
        required=True,
        id=graphene.Int(default_value=None),
        reference=graphene.String(default_value=None)
    )
    payment_confirmation = graphene.Field(
        Cart,
    )

    @staticmethod
    def resolve_payment_provider(self, info, id):
        env = info.context["env"]
        PaymentProvider = env['payment.provider'].sudo()
        domain = [
            ('id', '=', id),
            ('state', 'in', ['enabled', 'test']),
        ]

        payment_provider = PaymentProvider.search(domain, limit=1)
        if not payment_provider:
            raise GraphQLError(_('Payment provider does not exist.'))
        return payment_provider

    @staticmethod
    def resolve_payment_providers(self, info):
        env = info.context["env"]

        website = env['website'].get_current_website()
        order = website.sale_get_order()

        domain = Domain.AND([
            ['&', ('state', 'in', ['enabled', 'test']), ('company_id', '=', order.company_id.id)],
            ['|', ('website_id', '=', False), ('website_id', '=', website.id)],
            ['|', ('available_country_ids', '=', False), ('available_country_ids', 'in', [order.partner_id.country_id.id])]
        ])
        return env['payment.provider'].sudo().search(domain)

    @staticmethod
    def resolve_payment_transaction(self, info, id, reference):
        env = info.context["env"]
        PaymentTransaction = env['payment.transaction']

        if id:
            payment_transaction = PaymentTransaction.sudo().search([('id', '=', id)], limit=1)
        elif reference:
            payment_transaction = PaymentTransaction.sudo().search([('reference', '=', reference)], limit=1)
        else:
            payment_transaction = None

        if not payment_transaction:
            raise GraphQLError(_('Payment Transaction does not exist.'))
        return payment_transaction

    @staticmethod
    def resolve_payment_confirmation(self, info):
        env = info.context["env"]

        PaymentTransaction = env['payment.transaction']
        Order = env['sale.order'].sudo()

        # Pass in the session the sale_order created in alokai
        payment_transaction_id = request.session.get('__payment_monitored_tx_id__')
        order_id = request.session.get('sale_order_id')
        order = Order.search([('id', '=', order_id)], limit=1)

        if payment_transaction_id:
            payment_transaction = PaymentTransaction.sudo().search([('id', '=', payment_transaction_id)], limit=1)
            sale_order_id = payment_transaction.sale_order_ids.ids[0]

            if sale_order_id:
                order = Order.sudo().search([('id', '=', sale_order_id)], limit=1)

        if order.exists():
            return CartData(order=order)

        raise GraphQLError(_('Cart does not exist'))


class MakeGiftCardPayment(graphene.Mutation):
    done = graphene.Boolean()

    @staticmethod
    def mutate(self, info):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order()
        tx = order.get_portal_last_transaction()

        if order and not order.amount_total and not tx:
            order.with_context(send_email=True).action_confirm()
            return MakeGiftCardPayment(done=True)

        return MakeGiftCardPayment(done=False)


class PaymentMutation(graphene.ObjectType):
    make_gift_card_payment = MakeGiftCardPayment.Field(description='Pay the order only with gift card.')

query_registry.append(PaymentQuery)
mutation_registry.append(PaymentMutation)
