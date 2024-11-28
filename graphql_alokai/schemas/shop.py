# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError

from odoo.addons.graphql_alokai.schemas.objects import Order, Partner, Product
from odoo.addons.website_mass_mailing.controllers.main import MassMailController
from odoo.http import request
from odoo import _


class Cart(graphene.Interface):
    order = graphene.Field(Order)
    frequently_bought_together = graphene.List(Product)


class CartData(graphene.ObjectType):
    class Meta:
        interfaces = (Cart,)


class ShoppingCartQuery(graphene.ObjectType):
    cart = graphene.Field(
        Cart,
    )

    @staticmethod
    def resolve_cart(self, info):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=True)
        fbt = None

        if order and order.state != 'draft':
            request.session['sale_order_id'] = None
            order = website.sale_get_order(force_create=True)
        if order:
            order.order_line.filtered(lambda l: not l.product_id.active).unlink()

            fbt = order.\
                mapped('order_line').\
                mapped('product_id').\
                mapped('product_tmpl_id').\
                frequently_bought_together_ids.\
                sorted(key=lambda r: r.qty, reverse=True)
            fbt = fbt.mapped('related_product_id')

        return CartData(order=order, frequently_bought_together=fbt)


class CartClear(graphene.Mutation):
    Output = Order

    @staticmethod
    def mutate(self, info):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)
        order.order_line.sudo().unlink()
        return order


class SetShippingMethod(graphene.Mutation):
    class Arguments:
        shipping_method_id = graphene.Int(required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, shipping_method_id):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)

        delivery_method = env['delivery.carrier'].sudo().search([
            ('id', '=', shipping_method_id),
            ('website_published', '=', True)], limit=1
        )
        if not delivery_method:
            raise GraphQLError(_('Shipping method does not exist.'))
        rate = delivery_method.rate_shipment(order)
        order._set_delivery_method(delivery_method, rate=rate)

        return CartData(order=order)


# ---------------------------------------------------#
#      Additional Mutations that can be useful       #
# ---------------------------------------------------#

class ProductInput(graphene.InputObjectType):
    id = graphene.Int(required=True)
    quantity = graphene.Int(required=True)


class CartLineInput(graphene.InputObjectType):
    id = graphene.Int(required=True)
    quantity = graphene.Int(required=True)


class CartAddMultipleItems(graphene.Mutation):
    class Arguments:
        products = graphene.List(ProductInput, default_value={}, required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, products):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)
        # Forcing the website_id to be passed to the Order
        order.write({'website_id': website.id})
        for product in products:
            product_id = product['id']
            quantity = product['quantity']
            order._cart_update(product_id=product_id, add_qty=quantity)
        return CartData(order=order)


class CartUpdateMultipleItems(graphene.Mutation):
    class Arguments:
        lines = graphene.List(CartLineInput, default_value={}, required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, lines):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)
        for line in lines:
            line_id = line['id']
            quantity = line['quantity']
            line = order.order_line.filtered(lambda rec: rec.id == line_id)
            # Reset Warning Stock Message always before a new update
            line.shop_warning = ""
            order._cart_update(product_id=line.product_id.id, line_id=line.id, set_qty=quantity)
        return CartData(order=order)


class CartRemoveMultipleItems(graphene.Mutation):
    class Arguments:
        line_ids = graphene.List(graphene.Int, required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, line_ids):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)
        for line_id in line_ids:
            line = order.order_line.filtered(lambda rec: rec.id == line_id)
            line.unlink()
        return CartData(order=order)


class CreateUpdatePartner(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        email = graphene.String(required=True)
        subscribe_newsletter = graphene.Boolean(required=True)

    Output = Partner

    @staticmethod
    def mutate(self, info, name, email, subscribe_newsletter):
        env = info.context['env']
        website = env['website'].get_current_website()
        order = website.sale_get_order(force_create=1)

        data = {
            'name': name,
            'email': email,
        }

        partner = order.partner_id

        # Is public user
        if partner.id == website.user_id.sudo().partner_id.id:
            partner = env['res.partner'].sudo().create(data)

            order.write({
                'partner_id': partner.id,
                'partner_invoice_id': partner.id,
                'partner_shipping_id': partner.id,
            })
        else:
            partner.write(data)

        # Subscribe to newsletter
        if subscribe_newsletter:
            if website.alokai_mailing_list_id:
                MassMailController().subscribe(website.alokai_mailing_list_id.id, email, 'email')

        return partner


class ShopMutation(graphene.ObjectType):
    cart_clear = CartClear.Field(description="Cart Clear")
    cart_add_multiple_items = CartAddMultipleItems.Field(description="Add Multiple Items")
    cart_update_multiple_items = CartUpdateMultipleItems.Field(description="Update Multiple Items")
    cart_remove_multiple_items = CartRemoveMultipleItems.Field(description="Remove Multiple Items")
    set_shipping_method = SetShippingMethod.Field(description="Set Shipping Method on Cart")
    create_update_partner = CreateUpdatePartner.Field(description="Create or update a partner for guest checkout")
