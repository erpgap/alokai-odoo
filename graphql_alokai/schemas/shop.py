# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError

from odoo.addons.graphql_alokai.schemas.objects import Order, Partner, Product
from odoo.addons.website_mass_mailing.controllers.main import MassMailController
from odoo.addons.graphql_alokai.graphql.registry import query_registry, mutation_registry, type_registry
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
        order = website._get_and_cache_current_cart()
        fbt = None        

        if order and order.state != 'draft':
            request.session['sale_order_id'] = None
            order = website._get_and_cache_current_cart()
        if order:
            order.order_line.filtered(lambda l: not l.product_id.active).unlink()

            # User
            user = env['res.users'].sudo().search([('id', '=', env.uid)], limit=1)
            # When Cart is created by one Public User
            if not user:
                user = env.user
                # Claim the cart for this partner (recomputes addresses,
                # pricelist, fiscal position, taxes and prices).
                order._update_address(user.partner_id.id, ['partner_id'])

            fbt = order.\
                mapped('order_line').\
                mapped('product_id').\
                mapped('product_tmpl_id').\
                frequently_bought_together_ids.\
                sorted(key=lambda r: r.qty, reverse=True)
            fbt = fbt.mapped('related_product_id')[:12]

        # None when empty so the non-nullable Order.id isn't resolved on it.
        return CartData(order=order or None, frequently_bought_together=fbt)


class CartClear(graphene.Mutation):
    Output = Order

    @staticmethod
    def mutate(self, info):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website._get_and_cache_current_cart() or website._create_cart()
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
        order = website._get_and_cache_current_cart() or website._create_cart()

        delivery_method = env['delivery.carrier'].sudo().search([
            ('id', '=', shipping_method_id),
            ('is_published', '=', True)], limit=1
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
        order = website._get_and_cache_current_cart() or website._create_cart()
        # Bind the cart to the current website (it usually already is).
        if order.website_id != website:
            order.website_id = website.id

        # Add every line first, then run the cart-wide verification (delivery
        # rate recompute, ...) once, instead of re-verifying on every product.
        cart = order.with_context(skip_cart_verification=True)
        for product in products:
            cart._cart_add(product_id=product['id'], quantity=product['quantity'])
        order._verify_cart_after_update()

        fbt = order.\
            mapped('order_line').\
            mapped('product_id').\
            mapped('product_tmpl_id').\
            frequently_bought_together_ids.\
            sorted(key=lambda r: r.qty, reverse=True)
        fbt = fbt.mapped('related_product_id')[:12]
        return CartData(order=order, frequently_bought_together=fbt)


class CartUpdateMultipleItems(graphene.Mutation):
    class Arguments:
        lines = graphene.List(CartLineInput, default_value={}, required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, lines):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website._get_and_cache_current_cart() or website._create_cart()

        # Clear any stale stock warnings on the affected lines in one write,
        # then update each line, running the cart-wide verification once.
        target_ids = {line['id'] for line in lines}
        order.order_line.filtered(
            lambda l: l.id in target_ids and l.shop_warning).shop_warning = ""
        cart = order.with_context(skip_cart_verification=True)
        for line in lines:
            cart._cart_update_line_quantity(line_id=line['id'], quantity=line['quantity'])
        order._verify_cart_after_update()
        return CartData(order=order)


class CartRemoveMultipleItems(graphene.Mutation):
    class Arguments:
        line_ids = graphene.List(graphene.Int, required=True)

    Output = CartData

    @staticmethod
    def mutate(self, info, line_ids):
        env = info.context["env"]
        website = env['website'].get_current_website()
        order = website._get_and_cache_current_cart() or website._create_cart()
        # Remove all requested lines in a single unlink (one order recompute
        # instead of one per line, and no O(n*m) per-line scan).
        remove = set(line_ids)
        order.order_line.filtered(lambda l: l.id in remove).unlink()
        return CartData(order=order)


class CreateUpdatePartner(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        email = graphene.String(required=True)
        subscribe_newsletter = graphene.Boolean()
        phone = graphene.String()

    Output = Partner

    @staticmethod
    def mutate(self, info, name, email, subscribe_newsletter, phone=False):
        env = info.context['env']
        website = env['website'].get_current_website()
        order = website._get_and_cache_current_cart() or website._create_cart()

        data = {
            'name': name,
            'email': email,
        }
        if phone:
            data['phone'] = phone

        partner = order.partner_id

        # Is public user
        if partner.is_public_user:
            partner = env['res.partner'].sudo().create(data)

            order.write({
                'partner_id': partner.id,
                'partner_invoice_id': partner.id,
                'partner_shipping_id': partner.id,
            })
        else:
            partner.write(data)

            # Keep the logged-in user's login in sync with their email.
            # Login is part of the session token (see res.users
            # _get_session_token_fields), so changing it invalidates the
            # current session unless we refresh the token in place
            # (same pattern Odoo's own /my/security password change uses).
            user = env.user
            if not user._is_public() and user.partner_id == partner and email and user.login != email:
                existing = env['res.users'].sudo().search([('login', '=', email), ('id', '!=', user.id)], limit=1)
                if existing:
                    raise GraphQLError(_('This email is already used by another account.'))
                user.sudo().write({'login': email})
                if request:
                    new_token = user._compute_session_token(request.session.sid)
                    request.session.session_token = new_token

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


query_registry.append(ShoppingCartQuery)
mutation_registry.append(ShopMutation)
type_registry.append(CartData)
