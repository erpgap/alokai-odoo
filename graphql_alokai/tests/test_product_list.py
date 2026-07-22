# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Correctness coverage for ``get_product_list``.

On a controlled catalogue built in :meth:`setUpClass`, these tests pin the
observable behaviour of the product listing query: pagination, total count,
price sort, min/max price, the ``ids`` filter, the in-stock count and — most
importantly — the disjunctive attribute faceting (sibling values of a filtered
attribute keep their counts). They guard against regressions in the resolver.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.website_sale.tests.common import MockRequest
from odoo.addons.graphql_alokai.schemas.product import get_product_list


class _SortVal:
    """Stand-in for graphene's ``SortEnum`` member (only ``.value`` is used)."""
    def __init__(self, value):
        self.value = value


ASC = _SortVal('ASC')
DESC = _SortVal('DESC')


@tagged('post_install', '-at_install', 'alokai_product_list')
class TestProductList(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        Template = env['product.template']
        Attribute = env['product.attribute']
        Value = env['product.attribute.value']

        # --- Two variant-generating attributes -------------------------- #
        cls.color = Attribute.create({'name': 'ZZColor', 'create_variant': 'always'})
        cls.red, cls.green, cls.blue = Value.create([
            {'name': 'ZZRed', 'attribute_id': cls.color.id},
            {'name': 'ZZGreen', 'attribute_id': cls.color.id},
            {'name': 'ZZBlue', 'attribute_id': cls.color.id},
        ])
        cls.size = Attribute.create({'name': 'ZZSize', 'create_variant': 'always'})
        cls.s, cls.m, cls.l = Value.create([
            {'name': 'ZZS', 'attribute_id': cls.size.id},
            {'name': 'ZZM', 'attribute_id': cls.size.id},
            {'name': 'ZZL', 'attribute_id': cls.size.id},
        ])

        colors = [cls.red, cls.green, cls.blue]
        sizes = [cls.s, cls.m, cls.l]

        # --- A dedicated public category with filtering attributes ------ #
        cls.category = env['product.public.category'].create({
            'name': 'ZZ Test Category',
            'website_slug': '/zz-test-category',
            'attribute_ids': [(6, 0, (cls.color + cls.size).ids)],
        })

        # --- A controlled catalogue: volume + varied attributes/prices -- #
        # Enough volume to expose O(N) costs; varied attributes and prices
        # give meaningful facet counts.
        BULK = 60
        tmpl_vals = []
        for i in range(BULK):
            # Rotate which colour/size values each template carries so facet
            # counts differ across values.
            color_vals = colors[: (i % 3) + 1]          # 1..3 colours
            size_vals = sizes[: (i % 2) + 1]            # 1..2 sizes
            tmpl_vals.append({
                'name': 'ZZ Product %03d' % i,
                'list_price': 10.0 + i,                 # 10 .. 69, all distinct
                'is_published': True,
                'sale_ok': True,
                'public_categ_ids': [(6, 0, cls.category.ids)],
                'attribute_line_ids': [
                    (0, 0, {'attribute_id': cls.color.id,
                            'value_ids': [(6, 0, [v.id for v in color_vals])]}),
                    (0, 0, {'attribute_id': cls.size.id,
                            'value_ids': [(6, 0, [v.id for v in size_vals])]}),
                ],
            })
        cls.products = Template.create(tmpl_vals)
        cls.product_ids = cls.products.ids

        # --- An exactly-known sub-catalogue for facet-count assertions --- #
        # Four shirts in specific colour combinations (see the disjunctive
        # facet-count test). A and D are available in BOTH Red and Green.
        def shirt(name, color_vals):
            return {
                'name': name, 'list_price': 10.0, 'is_published': True,
                'sale_ok': True,
                'attribute_line_ids': [(0, 0, {
                    'attribute_id': cls.color.id,
                    'value_ids': [(6, 0, [v.id for v in color_vals])]})],
            }
        cls.shirts = Template.create([
            shirt('ZZ Shirt A', [cls.red, cls.green]),
            shirt('ZZ Shirt B', [cls.red]),
            shirt('ZZ Shirt C', [cls.green]),
            shirt('ZZ Shirt D', [cls.red, cls.green]),
        ])
        cls.shirt_ids = cls.shirts.ids

        cls.website = env['website'].get_current_website()
        env.flush_all()

        # Convenience id strings for the ``attrib_values`` filter, shaped as
        # the GraphQL layer delivers them: "<attribute_id>-<value_id>".
        cls.f_red = '%d-%d' % (cls.color.id, cls.red.id)
        cls.f_green = '%d-%d' % (cls.color.id, cls.green.id)
        cls.f_m = '%d-%d' % (cls.size.id, cls.m.id)

    def setUp(self):
        super().setUp()
        # The price-sort path resolves the current website/pricelist, which
        # only exist inside a request. Bind one for the whole test (the live
        # GraphQL resolver always runs with a request bound).
        req_cm = MockRequest(self.env, website=self.website)
        self.request = req_cm.__enter__()
        self.addCleanup(req_cm.__exit__, None, None, None)

    # ------------------------------------------------------------------ #
    #  Helpers                                                           #
    # ------------------------------------------------------------------ #
    def _call(self, fn, sort=None, page=1, page_size=20, search=False, **flt):
        return fn(self.env, page, page_size, search, sort or {}, **flt)

    # ------------------------------------------------------------------ #
    #  Correctness                                                       #
    # ------------------------------------------------------------------ #
    def test_ids_filter_returns_exactly_requested(self):
        wanted = self.product_ids[:5]
        products, total, *_ = self._call(get_product_list, sort={'id': ASC}, ids=wanted)
        self.assertEqual(sorted(p.id for p in products), sorted(wanted))
        self.assertEqual(total, len(wanted))

    def test_total_count_and_pagination(self):
        ids = self.product_ids
        page1 = self._call(get_product_list, sort={'id': ASC}, page=1, page_size=10, ids=ids)
        page2 = self._call(get_product_list, sort={'id': ASC}, page=2, page_size=10, ids=ids)
        self.assertEqual(page1[1], len(ids), "total_count must be the full match count")
        self.assertEqual(page2[1], len(ids), "total_count is independent of the page")
        self.assertEqual(len(page1[0]), 10)
        ids1 = [p.id for p in page1[0]]
        ids2 = [p.id for p in page2[0]]
        self.assertFalse(set(ids1) & set(ids2), "pages must not overlap")
        # id ASC over the first 20 by id
        self.assertEqual(ids1, sorted(ids)[:10])
        self.assertEqual(ids2, sorted(ids)[10:20])

    def test_price_sort_is_monotonic(self):
        ids = self.product_ids
        asc = self._call(get_product_list, sort={'price': ASC}, page_size=100, ids=ids)[0]
        desc = self._call(get_product_list, sort={'price': DESC}, page_size=100, ids=ids)[0]
        asc_prices = [p.list_price for p in asc]
        desc_prices = [p.list_price for p in desc]
        self.assertEqual(asc_prices, sorted(asc_prices))
        self.assertEqual(desc_prices, sorted(desc_prices, reverse=True))

    def test_min_max_price(self):
        ids = self.product_ids
        _, _, _, min_p, max_p, _ = self._call(get_product_list, sort={'id': ASC}, ids=ids)
        prices = self.products.mapped('list_price')
        self.assertEqual(round(min_p, 5), round(min(prices), 5))
        self.assertEqual(round(max_p, 5), round(max(prices), 5))

    def test_in_stock_filter_count_present(self):
        _, _, _, _, _, filter_counts = self._call(get_product_list, sort={'id': ASC})
        types = {d['type'] for d in filter_counts}
        self.assertIn('in_stock', types)

    def test_attribute_facets_present_for_category(self):
        _, _, attr_values, _, _, filter_counts = self._call(
            get_product_list, sort={'id': ASC}, category_id=self.category.ids)
        # Every colour and size value should surface as a facet.
        facet_ids = set(attr_values.ids)
        for v in (self.red, self.green, self.blue, self.s, self.m):
            self.assertIn(v.id, facet_ids)
        # And each facet carries an attribute_value count entry.
        counted = {d['id'] for d in filter_counts if d['type'] == 'attribute_value'}
        self.assertTrue(facet_ids <= counted)

    def test_disjunctive_facet_counts_are_not_double_counted(self):
        """Filtering on Red, the sibling colour Green must report its TRUE
        availability, counted once — not inflated by products available in
        both colours.

        Sub-catalogue (colours available): A=[Red,Green] B=[Red] C=[Green]
        D=[Red,Green]. With Color=Red selected:
          * Green is available in A, C, D  -> count MUST be 3 (not 5).
          * Red   is available in A, B, D  -> count 3.
        """
        _, _, attr_values, _, _, filter_counts = self._call(
            get_product_list, sort={'id': ASC},
            ids=self.shirt_ids, attrib_values=[self.f_red])
        self.assertIn(self.green.id, attr_values.ids,
                      "sibling colour must remain available as a facet")
        self.assertEqual(
            self._facet_count(filter_counts, self.green.id), 3,
            "Green is available in 3 shirts; it must not be double-counted to 5")
        self.assertEqual(self._facet_count(filter_counts, self.red.id), 3)

    @staticmethod
    def _facet_count(filter_counts, value_id):
        for d in filter_counts:
            if d['type'] == 'attribute_value' and d['id'] == value_id:
                return d['total']
        return None
