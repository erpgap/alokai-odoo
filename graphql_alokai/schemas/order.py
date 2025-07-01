# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
from odoo.http import request
from odoo import _
from odoo.addons.graphql_alokai.graphql.registry import query_registry, mutation_registry, type_registry
from odoo.addons.graphql_alokai.schemas.objects import (
    SortEnum, OrderStage, InvoiceStatus, Order, ShippingMethod,
    get_document_with_check_access,
    get_document_count_with_check_access
)


def get_search_order(sort):
    sorting = ''
    for field, val in sort.items():
        if sorting:
            sorting += ', '
        sorting += '%s %s' % (field, val.value)

    # Add id as last factor, so we can consistently get the same results
    if sorting:
        sorting += ', id ASC'
    else:
        sorting = 'id ASC'

    return sorting


class OrderFilterInput(graphene.InputObjectType):
    stages = graphene.List(OrderStage)
    invoice_status = graphene.List(InvoiceStatus)


class OrderSortInput(graphene.InputObjectType):
    id = SortEnum()
    date_order = SortEnum()
    name = SortEnum()
    state = SortEnum()


class Orders(graphene.Interface):
    orders = graphene.List(Order)
    total_count = graphene.Int(required=True)


class OrderList(graphene.ObjectType):
    class Meta:
        interfaces = (Orders,)


class OrderQuery(graphene.ObjectType):
    order = graphene.Field(
        Order,
        required=True,
        id=graphene.Int(),
    )
    orders = graphene.Field(
        Orders,
        filter=graphene.Argument(OrderFilterInput, default_value={}),
        current_page=graphene.Int(default_value=1),
        page_size=graphene.Int(default_value=10),
        sort=graphene.Argument(OrderSortInput, default_value={})
    )
    delivery_methods = graphene.List(
        graphene.NonNull(ShippingMethod)
    )

    @staticmethod
    def resolve_order(self, info, id):
        SaleOrder = info.context['env']['sale.order']
        error_msg = 'Sale Order does not exist.'
        order = get_document_with_check_access(SaleOrder, [('id', '=', id)], error_msg=error_msg)
        if not order:
            raise GraphQLError(_(error_msg))
        return order.sudo()

    @staticmethod
    def resolve_orders(self, info, filter, current_page, page_size, sort):
        env = info.context["env"]
        user = request.env.user
        partner = user.partner_id
        sort_order = get_search_order(sort)
        domain = [
            ('message_partner_ids', 'child_of', [partner.commercial_partner_id.id]),
        ]

        # Filter by stages or default to sales and done
        if filter.get('stages', False):
            stages = [stage.value for stage in filter['stages']]
            domain += [('state', 'in', stages)]
        else:
            domain += [('state', 'in', ['sale', 'done'])]

        # Filter by invoice status
        if filter.get('invoice_status', False):
            invoice_status = [invoice_status.value for invoice_status in filter['invoice_status']]
            domain += [('invoice_status', 'in', invoice_status)]

        # First offset is 0 but first page is 1
        if current_page > 1:
            offset = (current_page - 1) * page_size
        else:
            offset = 0

        SaleOrder = env["sale.order"]
        orders = get_document_with_check_access(SaleOrder, domain, sort_order, page_size, offset,
                                                error_msg='Sale Order does not exist.')
        total_count = get_document_count_with_check_access(SaleOrder, domain)
        return OrderList(orders=orders and orders.sudo() or orders, total_count=total_count)

    @staticmethod
    def resolve_delivery_methods(self, info):
        """ Get all shipping/delivery methods """
        env = info.context['env']
        website = env['website'].get_current_website()
        order = website.sale_get_order()
        if order:
            return order._get_delivery_methods()
        return env['delivery.carrier']


class ApplyCoupons(graphene.Interface):
    order = graphene.Field(Order)
    error = graphene.String()


class ApplyCouponList(graphene.ObjectType):
    class Meta:
        interfaces = (ApplyCoupons,)


class ApplyCoupon(graphene.Mutation):
    class Arguments:
        promo = graphene.String()

    Output = ApplyCouponList

    @staticmethod
    def mutate(self, info, promo):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)

        status = order._try_apply_code(promo)
        error = status.get('error')
        if 'error' in status:
            return ApplyCouponList(order=order, error=error)
        if not status:
            error = _('No reward to claim with this coupon')
            return ApplyCouponList(order=order, error=error)
        coupons = env['loyalty.card']
        rewards = env['loyalty.reward']
        for coupon, coupon_rewards in status.items():
            coupons |= coupon
            rewards |= coupon_rewards
        if len(coupons) == 1 and len(rewards) == 1:
            status = order._apply_program_reward(rewards.sudo(), coupons.sudo())
            if 'error' in status:
                error = status['error']
        return ApplyCouponList(order=order, error=error)


class ApplyGiftCards(graphene.Interface):
    order = graphene.Field(Order)
    error = graphene.String()


class ApplyGiftCardList(graphene.ObjectType):
    class Meta:
        interfaces = (ApplyGiftCards,)


class ApplyGiftCard(graphene.Mutation):
    class Arguments:
        promo = graphene.String()

    Output = ApplyGiftCardList

    @staticmethod
    def mutate(self, info, promo):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)

        status = order._try_apply_code(promo)
        error = status.get('error')
        if 'error' in status:
            return ApplyGiftCardList(order=order, error=error)
        if not status:
            error = _('No reward to claim with this gift card')
            return ApplyGiftCardList(order=order, error=error)
        coupons = env['loyalty.card']
        rewards = env['loyalty.reward']
        for coupon, coupon_rewards in status.items():
            coupons |= coupon
            rewards |= coupon_rewards
        if len(coupons) == 1 and len(rewards) == 1:
            status = order._apply_program_reward(rewards.sudo(), coupons.sudo())
            if 'error' in status:
                error = status['error']
        return ApplyGiftCardList(order=order, error=error)


class OrderMutation(graphene.ObjectType):
    apply_coupon = ApplyCoupon.Field(description='Apply Coupon')
    apply_gift_card = ApplyGiftCard.Field(description='Apply Gift Card')


query_registry.append(OrderQuery)
mutation_registry.append(OrderMutation)
type_registry.append(OrderList
                     )