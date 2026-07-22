# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
from odoo.http import request
from odoo import _
from odoo.addons.graphql_alokai.graphql.registry import query_registry, mutation_registry, type_registry
from odoo.addons.website_sale_wishlist.controllers.main import WebsiteSaleWishlist
from odoo.addons.graphql_alokai.schemas.objects import WishlistItem


class WishlistItems(graphene.Interface):
    wishlist_items = graphene.List(WishlistItem)
    total_count = graphene.Int(required=True)


class WishlistData(graphene.ObjectType):
    class Meta:
        interfaces = (WishlistItems,)


class WishlistQuery(graphene.ObjectType):
    wishlist_items = graphene.Field(
        WishlistData,
    )

    @staticmethod
    def resolve_wishlist_items(root, info):
        """ Get current user wishlist items """
        env = info.context['env']
        wishlist_items = env['product.wishlist'].current()
        total_count = len(wishlist_items)
        return WishlistData(wishlist_items=wishlist_items, total_count=total_count)


class WishlistAddItem(graphene.Mutation):
    class Arguments:
        product_id = graphene.Int(required=True)

    Output = WishlistData

    @staticmethod
    def mutate(self, info, product_id):
        env = info.context["env"]

        values = env['product.wishlist'].with_context(display_default_code=False).current()
        if values.filtered(lambda v: v.product_id.id == product_id):
            raise GraphQLError(_('Product already exists in the Wishlist.'))

        WebsiteSaleWishlist().add_to_wishlist(product_id)

        wishlist_items = env['product.wishlist'].current()
        total_count = len(wishlist_items)
        return WishlistData(wishlist_items=wishlist_items, total_count=total_count)


class WishlistRemoveItem(graphene.Mutation):
    class Arguments:
       wish_id = graphene.Int(required=True)

    Output = WishlistData

    @staticmethod
    def mutate(self, info, wish_id):
        env = info.context['env']

        # Only remove a row that belongs to the caller. current() is scoped to
        # the partner/session, so a foreign wish_id resolves to nothing rather
        # than letting anyone delete another customer's wishlist entry.
        wish = env['product.wishlist'].current().filtered(lambda w: w.id == wish_id)
        wish.sudo().unlink()

        wishlist_items = env['product.wishlist'].current()

        total_count = len(wishlist_items)
        return WishlistData(wishlist_items=wishlist_items, total_count=total_count)


class WishlistMutation(graphene.ObjectType):
    wishlist_add_item = WishlistAddItem.Field(description="Add Item")
    wishlist_remove_item = WishlistRemoveItem.Field(description="Remove Item")

query_registry.append(WishlistQuery)
mutation_registry.append(WishlistMutation)
type_registry.append(WishlistData)
