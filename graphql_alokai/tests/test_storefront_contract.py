# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""What the storefront relies on beyond "the query answers".

* ``website``: the storefront names its sitemap and reads per-website stock
  by the website Odoo resolves for the request.
* Product JSON-LD: a template with variants is a ProductGroup with one Offer
  per variant, priced as the shop shows it and linked by the URL the
  storefront builds (layers/product/utils mountUrlSlugForProductVariant).
* The image quality migration only moves the untouched default.
"""
import importlib.util
import json
import os

from odoo.modules.module import get_module_path
from odoo.tests import tagged

from .common import AlokaiGraphQLCommon


@tagged('post_install', '-at_install', 'alokai')
class TestStorefrontContract(AlokaiGraphQLCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        color = env['product.attribute'].create({
            'name': 'Color',
            'create_variant': 'always',
            'value_ids': [(0, 0, {'name': 'Off White'}), (0, 0, {'name': 'Black'})],
        })
        cls.grouped = env['product.template'].create({
            'name': 'Contract Grouped Tee',
            'list_price': 100.0,
            'sale_ok': True,
            'is_published': True,
            'attribute_line_ids': [(0, 0, {
                'attribute_id': color.id,
                'value_ids': [(6, 0, color.value_ids.ids)],
            })],
        })
        # Different prices, so each variant's own Offer is distinguishable.
        black = cls.grouped.attribute_line_ids.product_template_value_ids.filtered(
            lambda value: value.name == 'Black')
        black.price_extra = 10.0
        cls.single = env['product.template'].create({
            'name': 'Contract Single Mug',
            'list_price': 40.0,
            'sale_ok': True,
            'is_published': True,
        })

    def _product(self, template):
        body = self._gql("""
            query ($id: Int) { product(id: $id) {
              jsonLd slug
              productVariants { id combinationInfoVariant
                variantAttributeValues { id name attribute { name } } }
            } }
        """, {'id': template.id})
        product = body['data']['product']
        return json.loads(product['jsonLd']), product

    # ---------------------- website ---------------------------------- #
    def test_website_is_the_one_odoo_resolves(self):
        body = self._gql("query { website { id name } }")
        website = self.env['website'].get_current_website()
        self.assertEqual(body['data']['website']['id'], website.id)
        self.assertEqual(body['data']['website']['name'], website.name)

    # ---------------------- public partner --------------------------- #
    def test_only_the_websites_anonymous_partner_is_public(self):
        """Partner.isPublic tells the storefront the cart is anonymous: true
        for the website's public partner (whose user is archived), false for
        a customer's."""
        public = self.website.user_id.partner_id
        self.assertTrue(public._alokai_is_public())
        self.assertFalse(self.partner._alokai_is_public())
        self.assertTrue(self.website.user_id._is_public())
        self.assertFalse(self.user._is_public())

    # ---------------------- product JSON-LD -------------------------- #
    def test_variants_are_a_product_group(self):
        json_ld, product = self._product(self.grouped)

        self.assertEqual(json_ld['@type'], 'ProductGroup')
        self.assertEqual(json_ld['variesBy'], ['https://schema.org/color'])
        self.assertEqual(len(json_ld['hasVariant']), len(product['productVariants']))
        self.assertTrue(json_ld['url'].endswith(product['slug']))

    def test_each_variant_offer_is_priced_as_the_shop_shows_it(self):
        json_ld, product = self._product(self.grouped)

        offers = {item['color']: item['offers'] for item in json_ld['hasVariant']}
        for variant in product['productVariants']:
            color = variant['variantAttributeValues'][0]['name']
            shown = variant['combinationInfoVariant']['price']
            self.assertEqual(offers[color]['price'], f'{shown:.2f}')
        self.assertNotEqual(offers['Black']['price'], offers['Off White']['price'])

    def test_each_variant_offer_links_the_storefront_variant_url(self):
        json_ld, product = self._product(self.grouped)

        urls = {item['color']: item['offers']['url'] for item in json_ld['hasVariant']}
        for variant in product['productVariants']:
            value = variant['variantAttributeValues'][0]
            # mountUrlSlugForProductVariant: ?<attribute>=<slugified value>-<id>
            slug = value['name'].lower().replace(' ', '-')
            expected = f"{product['slug']}?color={slug}-{value['id']}"
            self.assertTrue(
                urls[value['name']].endswith(expected),
                f"{urls[value['name']]} does not end with {expected}")

    def test_single_variant_is_a_product_with_one_offer(self):
        json_ld, product = self._product(self.single)

        self.assertEqual(json_ld['@type'], 'Product')
        shown = product['productVariants'][0]['combinationInfoVariant']['price']
        self.assertEqual(json_ld['offers']['@type'], 'Offer')
        self.assertEqual(json_ld['offers']['price'], f'{shown:.2f}')

    # ---------------------- image quality migration ------------------ #
    def _run_quality_migration(self):
        path = os.path.join(
            get_module_path('graphql_alokai'), 'migrations', '19.0.1.3.0', 'post-migrate.py')
        spec = importlib.util.spec_from_file_location('alokai_quality_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(self.env.cr, '19.0.1.2.0')

    def _stored_quality(self):
        self.env.cr.execute(
            "SELECT value FROM ir_config_parameter WHERE key = 'alokai_image_quality'")
        return self.env.cr.fetchone()[0]

    def test_quality_migration_lowers_the_shipped_default(self):
        self.env['ir.config_parameter'].sudo().set_param('alokai_image_quality', '100')
        self.env.flush_all()
        self._run_quality_migration()
        self.assertEqual(self._stored_quality(), '80')

    def test_quality_migration_keeps_a_chosen_value(self):
        self.env['ir.config_parameter'].sudo().set_param('alokai_image_quality', '90')
        self.env.flush_all()
        self._run_quality_migration()
        self.assertEqual(self._stored_quality(), '90')
