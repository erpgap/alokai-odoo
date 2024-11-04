# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from odoo.osv import expression
from graphql import GraphQLError
from odoo import _
from collections import defaultdict
from graphene.types import generic
from odoo.addons.graphql_vuestorefront.schemas.objects import (
    SortEnum, Product, Attribute, AttributeValue
)


def get_product_list(env, current_page, page_size, search, sort, **kwargs):
    Product = env['product.template'].sudo()
    Category = env['product.public.category'].sudo()
    domain, attributes_partial_domain, prices_partial_domain, filtered_attributes = Product._graphql_get_search_domain(search, **kwargs)

    # First offset is 0 but first page is 1
    if current_page > 1:
        offset = (current_page - 1) * page_size
    else:
        offset = 0
    order = Product._graphql_get_search_order(sort)
    products = Product.search(domain, order=order)
    attribute_values = env['product.attribute.value'].sudo()
    filter_counts = []
    attribute_value_counts = defaultdict(int)

    if products:
        filtered_attribute_values = env['product.attribute.value'].sudo()
        # Attempt to get attribute values from category, otherwise fallback to attribute values from products
        category = None
        if kwargs.get('category_id'):
            category = Category.search([('id', 'in', kwargs['category_id'])], limit=1)
        elif kwargs.get('category_slug'):
            category = Category.search([('website_slug', '=', kwargs['category_slug'])], limit=1)
        if category:
            filtered_attribute_values = category.\
                mapped('attribute_ids').\
                mapped('value_ids').\
                filtered(lambda av: av.visibility and av.visibility == 'visible')

        # Attributes from category, they still need to be filtered based on the products we are returning
        if filtered_attribute_values:
            category_attribute_value_ids = filtered_attribute_values.ids
            product_attribute_value_ids = products.\
                mapped('variant_attribute_value_ids').\
                filtered(lambda av: av.visibility and av.visibility == 'visible').ids
            # Convert lists to sets and find the intersection to make sure attributes from category exist on products
            common_ids = list(set(category_attribute_value_ids) & set(product_attribute_value_ids))
            filtered_attribute_values = filtered_attribute_values.filtered(lambda av: av.id in common_ids)
        else:
            # Attributes from products
            filtered_attribute_values = products.\
                mapped('variant_attribute_value_ids').\
                filtered(lambda av: av.visibility and av.visibility == 'visible')

        # Our goal is to retrieve attribute values from filtered products to ensure that results are always returned,
        # regardless of the applied filters. A key challenge is that simply retrieving attribute values from filtered
        # products restricts us to values that match the filter criteria (e.g., filtering for green shoes only yields
        # the "green" color, excluding other colors).
        #
        # To address this, we’ll use the following approach:
        # 1. First, we collect all attribute values from the filtered products that were not included in the filter
        #    criteria.
        # 2. For each attribute, we retrieve products filtered by the remaining attributes, excluding the specific
        #    attribute in question.
        # 3. Finally, we cross-check these results to determine which attribute values within the attribute in question
        #    have products available.

        # Step 1.
        filtered_attribute_value_ids = list(set(num for sublist in filtered_attributes.values() for num in sublist))
        # Used for counting attribute values
        products_attribute_value_ids = [set(p.attribute_line_ids.value_ids.ids) for p in products]
        for filtered_attribute_value in filtered_attribute_values:
            if filtered_attribute_value.id not in filtered_attribute_value_ids:
                attribute_values |= filtered_attribute_value

                attribute_value_counts[filtered_attribute_value.id] += sum(
                    1
                    for products_attribute_value_id in products_attribute_value_ids
                    if filtered_attribute_value.id in products_attribute_value_id
                )

        # Step 2. and 3.
        for attribute_id, attribute_value_ids in filtered_attributes.items():
            domain = attributes_partial_domain.copy()
            attributes_domain = []

            # Step 2.
            for f_attribute_id, f_attribute_value_ids in filtered_attributes.items():
                if attribute_id == f_attribute_id:
                    continue
                attributes_domain.append([('attribute_line_ids.value_ids', 'in', f_attribute_value_ids)])

            attributes_domain = expression.AND(attributes_domain)
            domain.append(attributes_domain)
            domain = expression.AND(domain)

            partial_products = Product.search(domain)
            partial_attribute_values = partial_products.search(domain). \
                mapped('variant_attribute_value_ids'). \
                filtered(lambda av: av.attribute_id.id == attribute_id and av.visibility and av.visibility == 'visible')

            # Step 3.
            attribute_values |= partial_attribute_values

            # Used for counting attribute values
            products_attribute_value_ids = [set(p.attribute_line_ids.value_ids.ids) for p in partial_products]
            for partial_attribute_value in partial_attribute_values:
                attribute_value_counts[partial_attribute_value.id] += sum(
                    1
                    for products_attribute_value_id in products_attribute_value_ids
                    if partial_attribute_value.id in products_attribute_value_id
                    and partial_attribute_value.id in attribute_values.ids
                )

    # The partial domain is being used because when we select (example) attributes, the full list of products is
    # reduced which in turn also reduces the min and max prices
    if domain == prices_partial_domain:
        prices = products.mapped('list_price')
    else:
        prices = Product.search(prices_partial_domain).mapped('list_price')

    if prices:
        min_price = min(prices)
        max_price = max(prices)
    else:
        min_price = 0.0
        max_price = 0.0

    # Count filters
    if attribute_values:
        filter_counts.extend([{
            'type': 'attribute_value',
            'id': av.id,
            'total': attribute_value_counts[av.id],
        } for av in attribute_values])

    total_count = len(products)
    products = products[offset:offset + page_size]
    return products, total_count, attribute_values, min_price, max_price, filter_counts


class Products(graphene.Interface):
    products = graphene.List(Product)
    total_count = graphene.Int(required=True)
    attribute_values = graphene.List(AttributeValue)
    min_price = graphene.Float()
    max_price = graphene.Float()
    filter_counts = generic.GenericScalar()


class ProductList(graphene.ObjectType):
    class Meta:
        interfaces = (Products,)


class ProductFilterInput(graphene.InputObjectType):
    ids = graphene.List(graphene.Int)
    category_id = graphene.List(graphene.Int)
    category_slug = graphene.String()
    # Deprecated
    attribute_value_id = graphene.List(graphene.Int)
    attrib_values = graphene.List(graphene.String)
    name = graphene.String()
    min_price = graphene.Float()
    max_price = graphene.Float()


class ProductSortInput(graphene.InputObjectType):
    id = SortEnum()
    name = SortEnum()
    price = SortEnum()
    popular = SortEnum()
    newest = SortEnum()


class ProductVariant(graphene.Interface):
    product = graphene.Field(Product)
    product_template_id = graphene.Int()
    display_name = graphene.String()
    display_image = graphene.Boolean()
    price = graphene.Float()
    list_price = graphene.String()
    has_discounted_price = graphene.Boolean()
    is_combination_possible = graphene.Boolean()


class ProductVariantData(graphene.ObjectType):
    class Meta:
        interfaces = (ProductVariant,)


class ProductQuery(graphene.ObjectType):
    product = graphene.Field(
        Product,
        id=graphene.Int(default_value=None),
        slug=graphene.String(default_value=None),
        barcode=graphene.String(default_value=None),
    )
    products = graphene.Field(
        Products,
        filter=graphene.Argument(ProductFilterInput, default_value={}),
        current_page=graphene.Int(default_value=1),
        page_size=graphene.Int(default_value=20),
        search=graphene.String(default_value=False),
        sort=graphene.Argument(ProductSortInput, default_value={})
    )
    attribute = graphene.Field(
        Attribute,
        required=True,
        id=graphene.Int(),
    )
    product_variant = graphene.Field(
        ProductVariant,
        required=True,
        product_template_id=graphene.Int(),
        combination_id=graphene.List(graphene.Int)
    )

    @staticmethod
    def resolve_product(self, info, id=None, slug=None, barcode=None):
        env = info.context["env"]
        Product = env["product.template"].sudo()

        if id:
            product = Product.search([('id', '=', id)], limit=1)
        elif slug:
            product = Product.search([('website_slug', '=', slug)], limit=1)
        elif barcode:
            product = Product.search([('barcode', '=', barcode)], limit=1)
        else:
            product = Product

        if product:
            if not product.can_access_from_current_website():
                product = Product

        return product

    @staticmethod
    def resolve_products(self, info, filter, current_page, page_size, search, sort):
        env = info.context["env"]
        products, total_count, attribute_values, min_price, max_price, filter_counts = get_product_list(
            env, current_page, page_size, search, sort, **filter)
        return ProductList(products=products, total_count=total_count, attribute_values=attribute_values,
                           min_price=min_price, max_price=max_price, filter_counts=filter_counts)

    @staticmethod
    def resolve_attribute(self, info, id):
        return info.context["env"]["product.attribute"].search([('id', '=', id)], limit=1)

    @staticmethod
    def resolve_product_variant(self, info, product_template_id, combination_id=None):
        env = info.context["env"]

        is_combination_possible = False

        product_template = env['product.template'].browse(product_template_id)
        variant_info = product_template._get_combination_info()
        if combination_id:
            combination = env['product.template.attribute.value'].browse(combination_id)
            is_combination_possible = product_template._is_combination_possible(combination)

            variant_info = product_template._get_combination_info(combination)

            product = env['product.product'].browse(variant_info['product_id'])
        else:
            product = product_template.product_variant_id

        # Condition to verify if Product exist
        if not product:
            raise GraphQLError(_('Product does not exist'))

        # Condition to Verify if Product is active or if combination exist
        if not product.active or not is_combination_possible or not combination_id:
            variant_info['is_combination_possible'] = False
        else:
            variant_info['is_combination_possible'] = True

        return ProductVariantData(
            product=product,
            product_template_id=variant_info['product_template_id'],
            display_name=variant_info['display_name'],
            display_image=variant_info['display_image'],
            price=variant_info['price'],
            list_price=variant_info['list_price'],
            has_discounted_price=variant_info['has_discounted_price'],
            is_combination_possible=variant_info['is_combination_possible']
        )
