# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Popularity and frequently-bought-together, refreshed from recent sales."""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestSalesData(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        size = env['product.attribute'].create({'name': 'ZZSize', 'create_variant': 'always'})
        small, large = env['product.attribute.value'].create([
            {'name': 'ZZS', 'attribute_id': size.id},
            {'name': 'ZZL', 'attribute_id': size.id},
        ])
        category = env['product.public.category'].create({'name': 'ZZ Sales'})
        cls.shirt, cls.scarf = env['product.template'].create([{
            'name': 'ZZ Shirt', 'type': 'consu', 'public_categ_ids': [(6, 0, category.ids)],
            'attribute_line_ids': [(0, 0, {
                'attribute_id': size.id, 'value_ids': [(6, 0, (small + large).ids)]})],
        }, {
            'name': 'ZZ Scarf', 'type': 'consu', 'public_categ_ids': [(6, 0, category.ids)],
        }])
        shirt_s, shirt_l = cls.shirt.product_variant_ids
        partner = env['res.partner'].create({'name': 'ZZ Buyer'})

        def order(*lines):
            sale = env['sale.order'].create({
                'partner_id': partner.id,
                'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': qty})
                               for product, qty in lines],
            })
            sale.action_confirm()

        # The shirt sells 5: 1 of its first variant and 4 of the second, on
        # lines of different quantities. The scarf sells 1, with a shirt.
        order((shirt_s, 1), (shirt_l, 3))
        order((shirt_l, 1), (cls.scarf.product_variant_id, 1))
        env.flush_all()

        env['product.template']._cron_refresh_sales_data()
        env.invalidate_all()

    def test_popularity_counts_every_variant_and_line(self):
        """Popularity is the total sold over all variants, scaled to 100 for
        the best seller of the category."""
        self.assertEqual(self.shirt.recent_sales_count, 100)
        self.assertEqual(self.scarf.recent_sales_count, 20)

    def test_frequently_bought_together(self):
        """Products bought on the same order are related, both ways."""
        self.assertEqual(self.shirt.frequently_bought_together_ids.related_product_id, self.scarf)
        self.assertEqual(self.scarf.frequently_bought_together_ids.related_product_id, self.shirt)
