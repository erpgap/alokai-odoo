# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from werkzeug import urls

from odoo.fields import Domain
from graphql import GraphQLError
from odoo import _
from collections import defaultdict
from graphene.types import generic
from odoo.addons.graphql_alokai.graphql.registry import query_registry, type_registry
from odoo.addons.graphql_alokai.schemas.objects import (
    SortEnum, Product, Attribute, AttributeValue
)


def _price_bounds(Product, price_domain):
    """Min & max ``list_price`` over ``price_domain`` as one aggregate query.

    No records are materialised (mirrors the intent of Odoo core's shop
    price-range query). Returns ``(0.0, 0.0)`` when nothing matches.
    """
    [(min_price, max_price)] = Product._read_group(
        price_domain, aggregates=['list_price:min', 'list_price:max'])
    return float(min_price or 0.0), float(max_price or 0.0)


def _values_present_in(Product, domain):
    """Attribute values that actually occur in ``domain``'s result set.

    A single GROUP BY on the stored ``variant_attribute_value_ids`` relation
    instead of materialising every product and mapping in Python.
    """
    AttributeValue = Product.env['product.attribute.value'].sudo()
    values = AttributeValue.browse([
        value.id
        for [value] in Product._read_group(domain, groupby=['variant_attribute_value_ids'])
        if value
    ])
    return values.filtered(lambda av: av.visibility and av.visibility == 'visible')


def _count_templates_by_value(Product, count_domain, value_ids):
    """``{attribute_value_id: number of templates in ``count_domain`` whose
    attribute lines list that value}``.

    One grouped query replaces the legacy per-product Python set counting.
    """
    if not value_ids:
        return {}
    Line = Product.env['product.template.attribute.line'].sudo()
    template_query = Product._search(count_domain)
    groups = Line._read_group(
        [('product_tmpl_id', 'in', template_query), ('value_ids', 'in', list(value_ids))],
        groupby=['value_ids'],
        aggregates=['product_tmpl_id:count_distinct'],
    )
    return {value.id: count for value, count in groups}


def _price_sorted_page(env, Product, full_domain, sort, offset, page_size):
    """Return one page of products ordered by their pricelist price.

    Pricelist pricing is not a database column, so the whole matching set has
    to be ordered in Python. The prices are computed in a *single* batched
    pricelist call (``_get_products_price``) rather than one call per product.
    """
    website = env['website'].get_current_website()
    pricelist = website._get_and_cache_current_pricelist()
    products = Product.search(full_domain, order='id ASC')
    prices = pricelist._get_products_price(products.product_variant_id, 1.0)

    def price_of(product):
        return prices.get(product.product_variant_id.id, 0.0)

    if sort['price'].value == 'ASC':
        ordered = sorted(products, key=lambda p: (price_of(p), p.id))
    else:
        ordered = sorted(products, key=lambda p: (-price_of(p), p.id))

    page = ordered[offset:offset + page_size]
    return Product.browse([p.id for p in page])


def get_product_list(env, current_page, page_size, search, sort, **kwargs):
    Product = env['product.template'].sudo()
    Category = env['product.public.category'].sudo()
    AttributeValue = env['product.attribute.value'].sudo()

    domain, attributes_partial_domain, prices_partial_domain, filtered_attributes = \
        Product._graphql_get_search_domain(search, **kwargs)

    # First offset is 0 but first page is 1
    offset = (current_page - 1) * page_size if current_page > 1 else 0
    full_domain = Domain.AND(domain)

    total_count = Product.search_count(full_domain)

    # Min/max price are computed without the price filter so the slider keeps
    # its full range while the user drags it.
    min_price, max_price = _price_bounds(Product, Domain.AND(prices_partial_domain))

    # ------------------------------------------------------------------ #
    #  Attribute facets                                                  #
    # ------------------------------------------------------------------ #
    # Which values to expose, and the count per value.
    attribute_values = AttributeValue
    attribute_value_counts = defaultdict(int)

    if total_count:
        # The universe of facet values: prefer the category's configured
        # attribute values (so a category page always offers its full filter
        # set), intersected with what the current result set actually has;
        # otherwise fall back to the values present in the result set.
        present_values = _values_present_in(Product, full_domain)

        category = None
        if kwargs.get('category_id'):
            category = Category.search([('id', 'in', kwargs['category_id'])], limit=1)
        elif kwargs.get('category_slug'):
            category = Category.search([('website_slug', '=', kwargs['category_slug'])], limit=1)

        category_values = AttributeValue
        if category:
            category_values = category.mapped('attribute_ids').mapped('value_ids').filtered(
                lambda av: av.visibility and av.visibility == 'visible')

        if category_values:
            present_ids = set(present_values.ids)
            universe = category_values.filtered(lambda av: av.id in present_ids)
        else:
            universe = present_values

        # Step 1: values whose attribute is NOT being filtered, counted over
        # the full (filtered) result set. Values of a filtered attribute are
        # deliberately excluded here and handled by the disjunctive pass below
        # (Step 2), which lifts that attribute's own filter. Counting them in
        # both passes would double-count products that are available in both
        # the selected value and a sibling value.
        step1_values = universe.filtered(
            lambda av: av.attribute_id.id not in filtered_attributes)
        attribute_values |= step1_values
        counts = _count_templates_by_value(Product, full_domain, step1_values.ids)
        for av in step1_values:
            attribute_value_counts[av.id] += counts.get(av.id, 0)

        # Steps 2 & 3: for every actively-filtered attribute, recompute the
        # result set with *that* attribute's own filter lifted (disjunctive
        # faceting), so its sibling values show the count you would get by
        # selecting them instead.
        for attribute_id in filtered_attributes:
            partial_domain = attributes_partial_domain.copy()
            other_filters = [
                [('attribute_line_ids.value_ids', 'in', vids)]
                for other_id, vids in filtered_attributes.items()
                if other_id != attribute_id
            ]
            partial_domain.append(Domain.AND(other_filters))
            partial_domain = Domain.AND(partial_domain)

            partial_values = _values_present_in(Product, partial_domain).filtered(
                lambda av: av.attribute_id.id == attribute_id)
            attribute_values |= partial_values
            counts = _count_templates_by_value(Product, partial_domain, partial_values.ids)
            for av in partial_values:
                attribute_value_counts[av.id] += counts.get(av.id, 0)

    filter_counts = [{
        'type': 'attribute_value',
        'id': av.id,
        'total': attribute_value_counts[av.id],
    } for av in attribute_values]

    # ------------------------------------------------------------------ #
    #  The page of products                                              #
    # ------------------------------------------------------------------ #
    if 'price' in sort:
        products = _price_sorted_page(env, Product, full_domain, sort, offset, page_size)
    else:
        order = Product._graphql_get_search_order(sort)
        products = Product.search(full_domain, order=order, limit=page_size, offset=offset)

    # Count products in stock
    if kwargs.get('in_stock', False):
        filter_counts.append({
            'type': 'in_stock',
            'total': total_count,
        })
    else:
        website = env['website'].get_current_website()
        # TODO:
        # Possible index to improve performance
        # CREATE INDEX idx_redis_stock_website_quantity
        # ON product_template_redis_stock (website_id, quantity, product_id);
        env.cr.execute("""
            SELECT DISTINCT product_id
            FROM product_template_redis_stock
            WHERE website_id = %s AND quantity > 0
        """, (website.id,))
        in_stock_ids = [row[0] for row in env.cr.fetchall()]
        in_stock_domain = Domain.AND(domain + [[('id', 'in', in_stock_ids)]])
        filter_counts.append({
            'type': 'in_stock',
            'total': Product.search_count(in_stock_domain),
        })

    attribute_values = attribute_values.sorted(lambda av: (
        av.attribute_id.sequence, av.attribute_id.id, av.sequence, av.id))

    return products, total_count, attribute_values, min_price, max_price, filter_counts


class Products(graphene.Interface):
    products = graphene.List(Product)
    total_count = graphene.Int(required=True)
    attribute_values = graphene.List(AttributeValue)
    min_price = graphene.Float()
    max_price = graphene.Float()
    filter_counts = generic.GenericScalar()
    search_url = graphene.String()


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
    in_stock = graphene.Boolean()


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

        # None when empty so the nullable field resolves to null instead of
        # erroring on the non-nullable Product.id.
        return product or None

    @staticmethod
    def resolve_products(self, info, filter, current_page, page_size, search, sort):
        env = info.context["env"]
        products, total_count, attribute_values, min_price, max_price, filter_counts = get_product_list(
            env, current_page, page_size, search, sort, **filter)

        filter_data = {k.replace('_', '-'): v for k, v in filter.items()}
        filter_data['search'] = search
        ProductAttribute = env['product.attribute']
        if filter_data.get('attrib-values', False):
            for value in filter_data.pop('attrib-values'):
                try:
                    val = value.split('-')
                    if len(val) != 2:
                        continue
                    attribute_id = int(val[0])
                    attribute = ProductAttribute.search([('id', '=', attribute_id)])
                    if not attribute:
                        continue
                    attribute_name = attribute.name.lower()
                except ValueError:
                    continue

                if attribute_name not in filter_data:
                    filter_data[attribute_name] = []
                filter_data[attribute_name].append(value)

        search_url = urls.url_encode(dict(sorted(filter_data.items())))
        return ProductList(products=products, total_count=total_count, attribute_values=attribute_values,
                           min_price=min_price, max_price=max_price, filter_counts=filter_counts, search_url=search_url)

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
            display_image=bool(product.image_1920),
            price=variant_info['price'],
            list_price=variant_info['list_price'],
            has_discounted_price=variant_info['has_discounted_price'],
            is_combination_possible=variant_info['is_combination_possible']
        )


query_registry.append(ProductQuery)
type_registry.append(ProductList)
type_registry.append(ProductVariantData)
