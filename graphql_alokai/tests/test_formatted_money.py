# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Each monetary field has a ``formatted*`` sibling: the same amount as Odoo
formats it (currency symbol and position, decimals, language separators)."""
import json

from odoo.tests import tagged
from odoo.tests.common import HttpCase

from odoo.addons.graphql_alokai.schemas.objects import Invoice, Order, OrderLine, format_tax_totals


@tagged('post_install', '-at_install', 'alokai')
class TestFormattedMoney(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env['website'].get_current_website()
        cls.currency = cls.env.company.currency_id
        cls.product = cls.env['product.template'].create({
            'name': 'Formatted Money Mug', 'list_price': 1234.5,
            'sale_ok': True, 'is_published': True,
        })
        partner = cls.env['res.partner'].create({'name': 'Formatted Money Customer'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': partner.id,
            'order_line': [(0, 0, {'product_id': cls.product.product_variant_id.id, 'product_uom_qty': 2})],
        })
        cls.invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_line_ids': [(0, 0, {'name': 'Line', 'quantity': 1, 'price_unit': 10.0})],
        })

    def _gql(self, query, variables=None):
        response = self.url_open('/graphql/alokai', data=json.dumps({'query': query, 'variables': variables or {}}),
                                 headers={'Content-Type': 'application/json'})
        body = response.json()
        self.assertNotIn('errors', body, body.get('errors'))
        return body['data']

    def test_product_prices(self):
        product = self._gql("""
            query ($id: Int) { product(id: $id) {
              price formattedPrice combinationInfo
              firstVariant { variantPrice formattedVariantPrice }
            } }
        """, {'id': self.product.id})['product']
        self.assertEqual(product['formattedPrice'], self.currency.format(product['price']))
        info = product['combinationInfo']
        currency = self.website.currency_id
        self.assertEqual(info['formatted_discount'], currency.format(info['discount']))
        self.assertEqual(info['formatted_price'], currency.format(info['price']))
        variant = product['firstVariant']
        self.assertEqual(variant['formattedVariantPrice'], currency.format(variant['variantPrice']))

        listing = self._gql("query { products(pageSize: 5) { minPrice formattedMinPrice } }")['products']
        self.assertEqual(listing['formattedMinPrice'], self.currency.format(listing['minPrice']))

    def test_order_and_invoice(self):
        order, currency = self.order, self.order.currency_id
        self.assertEqual(Order.resolve_formatted_amount_total(order, None), currency.format(order.amount_total))
        self.assertEqual(Invoice.resolve_formatted_amount_total(self.invoice, None),
                         self.invoice.currency_id.format(self.invoice.amount_total))

    def test_follows_the_currency_format(self):
        self.currency.write({'position': 'before', 'symbol': 'Ⓧ'})
        self.assertTrue(Order.resolve_formatted_amount_total(self.order, None).startswith('Ⓧ'))

    def test_tax_totals(self):
        currency = self.order.currency_id
        tax_totals = format_tax_totals(self.order.tax_totals, currency)
        self.assertEqual(tax_totals['formatted_total_amount_currency'],
                         currency.format(tax_totals['total_amount_currency']))
        for subtotal in tax_totals['subtotals']:
            self.assertEqual(subtotal['formatted_base_amount_currency'],
                             currency.format(subtotal['base_amount_currency']))
            for group in subtotal['tax_groups']:
                # Odoo's own numeric display_* keys are left untouched.
                self.assertIsInstance(group['display_base_amount_currency'], (int, float))

    def test_cart_line_prices(self):
        """A cart line shows Odoo's cart prices: its price, and before a
        discount the struck-through price and what the discount takes off."""
        product = self.env['product.template'].create({
            'name': 'Discounted Line Mug', 'list_price': 100.0, 'is_published': True, 'taxes_id': [(5, 0, 0)],
        })
        order = self.env['sale.order'].create({
            'partner_id': self.order.partner_id.id,
            'website_id': self.website.id,
            'order_line': [(0, 0, {'product_id': product.product_variant_id.id, 'product_uom_qty': 2, 'discount': 10})],
        })
        line, currency = order.order_line, order.currency_id
        # No taxes: before the discount is the unit price (in the order's
        # pricelist currency) times the quantity, and the cart shows 10% less.
        before = currency.round(line.price_unit * 2)
        self.assertAlmostEqual(line.price_subtotal, before * 0.9, places=2)
        self.assertEqual(OrderLine.resolve_formatted_price_before_discount(line, None), currency.format(before))
        self.assertEqual(OrderLine.resolve_formatted_cart_price(line, None), currency.format(line.price_subtotal))
        self.assertEqual(OrderLine.resolve_formatted_discount_amount(line, None),
                         currency.format(before - currency.round(line.price_subtotal)))

        line.discount = 0
        self.assertEqual(OrderLine.resolve_formatted_cart_price(line, None), currency.format(before))
        self.assertIsNone(OrderLine.resolve_formatted_price_before_discount(line, None))
        self.assertIsNone(OrderLine.resolve_formatted_discount_amount(line, None))
