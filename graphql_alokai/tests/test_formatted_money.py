# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Each monetary field has a ``formatted*`` sibling: the same amount as Odoo
formats it (currency symbol and position, decimals, language separators)."""
from odoo.tests import tagged

from odoo.addons.graphql_alokai.schemas.objects import OrderLine

from .common import AlokaiGraphQLCommon


@tagged('post_install', '-at_install', 'alokai')
class TestFormattedMoney(AlokaiGraphQLCommon):

    def _fmt(self, amount, currency):
        lang = self.website.default_lang_id.code
        return currency.with_context(lang=lang).format(amount)

    def test_order_amounts_lines_and_tax_totals(self):
        self._login()
        order = self._gql("""
            query ($id: Int) { order(id: $id) {
              amountTotal formattedAmountTotal
              amountUntaxed formattedAmountUntaxed
              amountSubtotal formattedAmountSubtotal
              taxTotals
              currency { name symbol position decimalPlaces rounding decimalSeparator thousandsSeparator }
              orderLines { priceUnit formattedPriceUnit priceTotal formattedPriceTotal }
            } }
        """, {'id': self.sale_order.id})['data']['order']
        currency = self.sale_order.currency_id
        self.assertEqual(order['formattedAmountTotal'], self._fmt(order['amountTotal'], currency))
        self.assertEqual(order['formattedAmountUntaxed'], self._fmt(order['amountUntaxed'], currency))
        self.assertEqual(order['formattedAmountSubtotal'], self._fmt(order['amountSubtotal'], currency))
        self.assertIn(currency.symbol, order['formattedAmountTotal'])
        self.assertEqual(order['currency']['position'], currency.position)
        self.assertEqual(order['currency']['decimalPlaces'], currency.decimal_places)
        lang = self.env['res.lang']._lang_get(self.website.default_lang_id.code)
        self.assertEqual(order['currency']['decimalSeparator'], lang.decimal_point)
        self.assertEqual(order['currency']['thousandsSeparator'], lang.thousands_sep)
        for line in order['orderLines']:
            self.assertEqual(line['formattedPriceUnit'], self._fmt(line['priceUnit'], currency))
            self.assertEqual(line['formattedPriceTotal'], self._fmt(line['priceTotal'], currency))

        tax_totals = order['taxTotals']
        self.assertEqual(tax_totals['formatted_total_amount_currency'],
                         self._fmt(tax_totals['total_amount_currency'], currency))
        for subtotal in tax_totals['subtotals']:
            self.assertEqual(subtotal['formatted_base_amount_currency'],
                             self._fmt(subtotal['base_amount_currency'], currency))
            for group in subtotal['tax_groups']:
                # Odoo's own numeric display_* keys are left untouched.
                self.assertIsInstance(group['display_base_amount_currency'], (int, float))
                self.assertEqual(group['formatted_tax_amount_currency'],
                                 self._fmt(group['tax_amount_currency'], currency))

    def test_follows_the_currency_format(self):
        """Symbol position and decimals come from the currency, not a guess."""
        self._login()
        currency = self.invoice.currency_id
        currency.write({'position': 'before', 'symbol': 'Ⓧ'})
        invoice = self._gql("""
            query ($id: Int) { invoice(id: $id) {
              amountTotal formattedAmountTotal formattedAmountResidual
              invoiceLines { priceUnit formattedPriceUnit }
            } }
        """, {'id': self.invoice.id})['data']['invoice']
        self.assertTrue(invoice['formattedAmountTotal'].startswith('Ⓧ'))
        self.assertEqual(invoice['formattedAmountTotal'], self._fmt(invoice['amountTotal'], currency))
        self.assertIsNotNone(invoice['formattedAmountResidual'])
        for line in invoice['invoiceLines']:
            self.assertEqual(line['formattedPriceUnit'], self._fmt(line['priceUnit'], currency))

    def test_product_prices(self):
        currency = self.website.currency_id
        product = self._gql("""
            query ($id: Int) { product(id: $id) {
              price formattedPrice combinationInfo
              firstVariant { variantPrice formattedVariantPrice
                             variantPriceAfterDiscount formattedVariantPriceAfterDiscount }
            } }
        """, {'id': self.barcode_product.id})['data']['product']
        self.assertEqual(product['formattedPrice'], self._fmt(product['price'], currency))
        info = product['combinationInfo']
        self.assertEqual(info['formatted_discount'], currency.format(info['discount']))
        self.assertEqual(info['formatted_price'], self._fmt(info['price'], currency))
        self.assertEqual(info['formatted_list_price'], self._fmt(info['list_price'], currency))
        variant = product['firstVariant']
        self.assertEqual(variant['formattedVariantPrice'], self._fmt(variant['variantPrice'], currency))
        self.assertEqual(variant['formattedVariantPriceAfterDiscount'],
                         self._fmt(variant['variantPriceAfterDiscount'], currency))

        variant_data = self._gql("""
            query ($tmpl: Int) { productVariant(productTemplateId: $tmpl) {
              price formattedPrice formattedListPrice
            } }
        """, {'tmpl': self.barcode_product.id})['data']['productVariant']
        self.assertEqual(variant_data['formattedPrice'], self._fmt(variant_data['price'], currency))
        self.assertIsNotNone(variant_data['formattedListPrice'])

        listing = self._gql("""
            query { products(pageSize: 5) { minPrice maxPrice formattedMinPrice formattedMaxPrice } }
        """)['data']['products']
        self.assertEqual(listing['formattedMinPrice'], self._fmt(listing['minPrice'], currency))
        self.assertEqual(listing['formattedMaxPrice'], self._fmt(listing['maxPrice'], currency))

    def test_absent_amount_stays_null(self):
        """Attribute values without an extra price have no formatted one."""
        product = self._gql("""
            query ($id: Int) { product(id: $id) {
              attributeValues { priceExtra formattedPriceExtra }
            } }
        """, {'id': self.barcode_product.id})['data']['product']
        for value in product['attributeValues'] or []:
            if value['priceExtra'] is None:
                self.assertIsNone(value['formattedPriceExtra'])

    def test_cart_line_prices(self):
        """A cart line shows Odoo's cart prices: its price, and before a
        discount the struck-through price and what the discount takes off."""
        product = self.env['product.template'].create({
            'name': 'Discounted Line Mug', 'list_price': 100.0, 'is_published': True, 'taxes_id': [(5, 0, 0)],
        })
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
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
