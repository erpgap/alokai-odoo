# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestAlokaiWishlistAcrossWebsites(TransactionCase):
    """A customer's wishlist is shared by the websites: each website shows the products it
    sells. Product A is sold on both websites, B only on website 1 and C only on website 2."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Website = cls.env['website']
        cls.website_1 = Website.create({'name': 'Alokai Wishlist Website 1'})
        cls.website_2 = Website.create({'name': 'Alokai Wishlist Website 2'})
        cls.partner = cls.env.user.partner_id
        cls.Wishlist = cls.env['product.wishlist']
        cls.Wishlist.sudo().with_context(active_test=False).search([('partner_id', '=', cls.partner.id)]).unlink()

        def product(name, website):
            return cls.env['product.product'].create({
                'name': name, 'list_price': 50.0, 'sale_ok': True, 'is_published': True,
                'website_id': website.id if website else False,
            })
        cls.product_a = product('Wishlist A (both websites)', False)
        cls.product_b = product('Wishlist B (website 1 only)', cls.website_1)
        cls.product_c = product('Wishlist C (website 2 only)', cls.website_2)

    def _add(self, product, website):
        return self.Wishlist._add_to_wishlist(
            False, website.currency_id.id, website.id, 50.0, product.id, self.partner.id)

    def _shown_on(self, website):
        """The products the wishlist shows on `website`, as during a request to it."""
        fake_request = SimpleNamespace(
            website=SimpleNamespace(id=website.id, is_public_user=lambda: False), session={})
        with patch('odoo.addons.graphql_alokai.models.product_wishlist.request', fake_request), \
                patch('odoo.addons.website_sale_wishlist.models.product_wishlist.request', fake_request):
            return self.Wishlist.with_context(website_id=website.id).current().product_id

    def test_product_sold_on_both_websites_shows_on_both(self):
        self._add(self.product_a, self.website_1)
        self.assertIn(self.product_a, self._shown_on(self.website_1))
        self.assertIn(self.product_a, self._shown_on(self.website_2))

    def test_product_sold_on_one_website_shows_only_there(self):
        self._add(self.product_b, self.website_1)
        self._add(self.product_c, self.website_2)
        self.assertIn(self.product_b, self._shown_on(self.website_1))
        self.assertNotIn(self.product_b, self._shown_on(self.website_2))
        self.assertIn(self.product_c, self._shown_on(self.website_2))
        self.assertNotIn(self.product_c, self._shown_on(self.website_1))

    def test_adding_again_on_the_other_website_keeps_it_on_both(self):
        """It used to fail with a duplicate key error, and must not move the product either."""
        first = self._add(self.product_a, self.website_1)
        again = self._add(self.product_a, self.website_2)
        self.assertEqual(again, first)
        self.assertEqual(first.website_id, self.website_1)
        self.assertIn(self.product_a, self._shown_on(self.website_1))
        self.assertIn(self.product_a, self._shown_on(self.website_2))

    def test_archived_product_is_reactivated(self):
        first = self._add(self.product_a, self.website_1)
        first.active = False
        self.assertEqual(self._add(self.product_a, self.website_1), first)
        self.assertTrue(first.active)
