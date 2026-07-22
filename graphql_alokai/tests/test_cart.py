# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the multi-item cart mutations.

The add/update mutations used to call Odoo's per-item cart methods in a loop,
each of which re-ran the cart-wide verification (delivery-rate recompute, an
external carrier call for real carriers) -- once per item. These tests pin the
fixed behaviour: the verification runs exactly once per mutation, and the cart
ends up with the correct lines/quantities.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.website_sale.tests.common import MockRequest
from odoo.addons.graphql_alokai.schemas.shop import (
    CartAddMultipleItems, CartUpdateMultipleItems, CartRemoveMultipleItems)


class _Info:
    def __init__(self, env):
        self.context = {'env': env}


@tagged('post_install', '-at_install', 'alokai_cart')
class TestCartMutations(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.website = env['website'].get_current_website()
        cls.products = env['product.template'].create([
            {'name': 'ZZ Cart P%d' % i, 'list_price': 10.0 + i,
             'is_published': True, 'sale_ok': True}
            for i in range(3)
        ])
        cls.variants = [p.product_variant_id for p in cls.products]
        cls.SaleOrder = type(env['sale.order'])

    def setUp(self):
        super().setUp()
        # A fresh empty cart per test.
        self.cart = self.env['sale.order'].create({
            'partner_id': self.env.user.partner_id.id,
            'website_id': self.website.id,
        })
        self.info = _Info(self.env)

    def _count_verifications(self):
        """Context manager patching _verify_cart_after_update to count calls
        while still running the real implementation."""
        original = self.SaleOrder._verify_cart_after_update
        calls = []

        def counting(order_self, *args, **kwargs):
            calls.append(1)
            return original(order_self, *args, **kwargs)

        return patch.object(
            self.SaleOrder, '_verify_cart_after_update', counting), calls

    def test_add_multiple_verifies_once(self):
        products = [{'id': v.id, 'quantity': i + 1} for i, v in enumerate(self.variants)]
        patcher, calls = self._count_verifications()
        with MockRequest(self.env, website=self.website, sale_order_id=self.cart.id), patcher:
            CartAddMultipleItems.mutate(None, self.info, products)

        self.assertEqual(len(calls), 1, "verification must run once, not once per item")
        # All three products are in the cart with the requested quantities.
        by_product = {l.product_id.id: l.product_uom_qty for l in self.cart.order_line}
        for i, v in enumerate(self.variants):
            self.assertEqual(by_product.get(v.id), i + 1)

    def test_update_multiple_verifies_once(self):
        # Seed the cart with all three products (qty 1 each).
        with MockRequest(self.env, website=self.website, sale_order_id=self.cart.id):
            CartAddMultipleItems.mutate(
                None, self.info, [{'id': v.id, 'quantity': 1} for v in self.variants])

        lines = self.cart.order_line
        updates = [{'id': line.id, 'quantity': 5} for line in lines]
        patcher, calls = self._count_verifications()
        with MockRequest(self.env, website=self.website, sale_order_id=self.cart.id), patcher:
            CartUpdateMultipleItems.mutate(None, self.info, updates)

        self.assertEqual(len(calls), 1, "verification must run once for the whole update")
        self.assertTrue(all(l.product_uom_qty == 5 for l in self.cart.order_line))

    def test_remove_multiple_removes_all_lines(self):
        with MockRequest(self.env, website=self.website, sale_order_id=self.cart.id):
            CartAddMultipleItems.mutate(
                None, self.info, [{'id': v.id, 'quantity': 1} for v in self.variants])
        line_ids = self.cart.order_line.ids
        self.assertEqual(len(line_ids), 3)

        with MockRequest(self.env, website=self.website, sale_order_id=self.cart.id):
            CartRemoveMultipleItems.mutate(None, self.info, line_ids[:2])

        remaining = self.cart.order_line.ids
        self.assertEqual(set(remaining), {line_ids[2]}, "only the un-removed line stays")
