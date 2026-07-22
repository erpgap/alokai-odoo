# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the per-request resolver cache (pricing + wishlist).

Two things are proven:

1. **Correctness** — the cached pricing info matches Odoo's own computation
   (so shoppers see the right price), and wishlist membership is reported
   correctly for products that are / are not in the wishlist.
2. **De-duplication** — the expensive work runs once per request: the pricing
   info is computed once per product (identical object reused, no extra
   queries), and the wishlist is loaded once (repeated checks hit no database).
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.website_sale.tests.common import MockRequest
from odoo.addons.graphql_alokai.schemas import request_cache

# A minimal valid 1x1 PNG (already base64-encoded), for a variant image.
_ONE_PX_PNG = (
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhf'
    b'DwAChwGA60e6kgAAAABJRU5ErkJggg==')


class _Info:
    """Minimal stand-in for graphene's ResolveInfo: resolvers only touch
    ``info.context``, which the controller builds as ``{"env": env}``."""
    def __init__(self, env):
        self.context = {'env': env}


@tagged('post_install', '-at_install', 'alokai_request_cache')
class TestRequestCache(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.website = env['website'].get_current_website()

        cls.product = env['product.template'].create({
            'name': 'RC Wishlisted', 'list_price': 42.0,
            'is_published': True, 'sale_ok': True,
        })
        cls.other = env['product.template'].create({
            'name': 'RC Not Wishlisted', 'list_price': 7.0,
            'is_published': True, 'sale_ok': True,
        })
        # A product whose default variant carries a variant-specific image, and
        # one without (self.product), to exercise first_variant_with_image.
        cls.product_with_variant_image = env['product.template'].create({
            'name': 'RC Variant Image', 'list_price': 5.0,
            'is_published': True, 'sale_ok': True,
        })
        cls.product_with_variant_image.product_variant_id.image_variant_1920 = _ONE_PX_PNG
        # A wishlist entry owned by the current (internal) user's partner, so
        # product.wishlist.current() picks it up under MockRequest.
        cls.wishlist = env['product.wishlist'].create({
            'partner_id': env.user.partner_id.id,
            'product_id': cls.product.product_variant_id.id,
            'website_id': cls.website.id,
        })
        env.flush_all()

    def setUp(self):
        super().setUp()
        req_cm = MockRequest(self.env, website=self.website)
        self.request = req_cm.__enter__()
        self.addCleanup(req_cm.__exit__, None, None, None)
        self.info = _Info(self.env)

    # ------------------------------------------------------------------ #
    #  Pricing                                                           #
    # ------------------------------------------------------------------ #
    def test_pricing_info_matches_direct_computation(self):
        """The cached price is exactly what Odoo computes directly."""
        cached = request_cache.get_pricing_info(self.info, self.product)
        direct = self.product._get_combination_info()
        self.assertEqual(cached['list_price'], direct['list_price'])
        self.assertEqual(cached['price'], direct['price'])
        self.assertEqual(cached['has_discounted_price'], direct['has_discounted_price'])

    def test_pricing_info_computed_once_per_product(self):
        """Second lookup reuses the cached dict and issues no new query."""
        first = request_cache.get_pricing_info(self.info, self.product)
        q0 = self.env.cr.sql_log_count
        second = request_cache.get_pricing_info(self.info, self.product)
        self.assertIs(first, second, "pricing must be cached, not recomputed")
        self.assertEqual(self.env.cr.sql_log_count, q0,
                         "a cached pricing lookup must not hit the database")

    def test_pricing_info_is_per_product(self):
        """Different products get their own (correct) cache entries."""
        a = request_cache.get_pricing_info(self.info, self.product)
        b = request_cache.get_pricing_info(self.info, self.other)
        self.assertIsNot(a, b)
        self.assertEqual(a['list_price'], self.product._get_combination_info()['list_price'])
        self.assertEqual(b['list_price'], self.other._get_combination_info()['list_price'])

    def test_cached_dict_survives_serialising_copy(self):
        """Callers copy the dict before rewriting keys; the cache stays intact
        so other price resolvers still read the original values."""
        cached = request_cache.get_pricing_info(self.info, self.product)
        original_list_price = cached['list_price']
        # Emulate resolve_combination_info: copy, then rewrite keys.
        serialised = dict(cached)
        serialised['currency'] = {'id': 1, 'name': 'X', 'symbol': 'X'}
        serialised['list_price'] = -999
        again = request_cache.get_pricing_info(self.info, self.product)
        self.assertEqual(again['list_price'], original_list_price)
        self.assertNotEqual(again.get('currency'), {'id': 1, 'name': 'X', 'symbol': 'X'})

    # ------------------------------------------------------------------ #
    #  Wishlist                                                          #
    # ------------------------------------------------------------------ #
    def test_wishlist_membership_is_correct(self):
        self.assertTrue(
            request_cache.is_in_wishlist(self.info, self.product),
            "a wishlisted product template must report True")
        self.assertTrue(
            request_cache.is_in_wishlist(self.info, self.product.product_variant_id),
            "the wishlisted variant must report True too")
        self.assertFalse(
            request_cache.is_in_wishlist(self.info, self.other),
            "a product not in the wishlist must report False")

    def test_wishlist_loaded_once_per_request(self):
        """After the first check, further checks hit no database."""
        request_cache.is_in_wishlist(self.info, self.product)   # warm the cache
        q0 = self.env.cr.sql_log_count
        for _ in range(10):
            request_cache.is_in_wishlist(self.info, self.product)
            request_cache.is_in_wishlist(self.info, self.other)
        self.assertEqual(self.env.cr.sql_log_count, q0,
                         "wishlist membership checks must not re-query")

    # ------------------------------------------------------------------ #
    #  Current website                                                   #
    # ------------------------------------------------------------------ #
    def test_current_website_is_correct_and_cached(self):
        website = request_cache.current_website(self.info)
        self.assertEqual(website, self.website)
        q0 = self.env.cr.sql_log_count
        again = request_cache.current_website(self.info)
        self.assertIs(website, again, "the website must be the cached instance")
        self.assertEqual(self.env.cr.sql_log_count, q0,
                         "a cached website lookup must not hit the database")

    # ------------------------------------------------------------------ #
    #  First variant with image                                          #
    # ------------------------------------------------------------------ #
    def test_first_variant_with_image_is_correct(self):
        self.assertEqual(
            request_cache.first_variant_with_image(self.info, self.product_with_variant_image),
            self.product_with_variant_image.product_variant_id,
            "must return the variant that carries the image")
        self.assertIsNone(
            request_cache.first_variant_with_image(self.info, self.product),
            "a product without a variant image must return None")

    def test_first_variant_with_image_computed_once(self):
        first = request_cache.first_variant_with_image(self.info, self.product_with_variant_image)
        q0 = self.env.cr.sql_log_count
        again = request_cache.first_variant_with_image(self.info, self.product_with_variant_image)
        self.assertIs(first, again, "must reuse the cached result")
        self.assertEqual(self.env.cr.sql_log_count, q0,
                         "a cached image lookup must not hit the database")
