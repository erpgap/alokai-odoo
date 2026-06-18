# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the module's non-GraphQL HTTP surface and host routing:

* the ``/web/image`` override that rejects oversized resize requests,
* the ``/checkout-redirect`` route,
* the ``website._alokai_resolve_by_host`` storefront routing helper.
"""

from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import AlokaiGraphQLCommon


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiControllers(AlokaiGraphQLCommon):

    # ------------------------- /web/image guard -------------------- #
    def test_image_resize_limit_blocks_oversize(self):
        """A resize larger than alokai_image_resize_limit must 404."""
        self.env['ir.config_parameter'].sudo().set_param(
            'alokai_image_resize_limit', 1920)
        # Use a published product image (public-readable, as on the storefront).
        url = '/web/image/product.template/%s/image_1920/9999x9999' % self.product.id
        response = self.url_open(url)
        self.assertEqual(response.status_code, 404)

    def test_image_resize_allows_normal(self):
        """A resize within the limit is served normally."""
        url = '/web/image/product.template/%s/image_1920/64x64' % self.product.id
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200)

    # ------------------------- /checkout-redirect ------------------ #
    def test_checkout_redirect(self):
        """Without an access token the route redirects to /shop/checkout."""
        response = self.url_open('/checkout-redirect', allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))
        self.assertIn('/shop/checkout', response.headers.get('Location', ''))

    # ------------------------- host routing ------------------------ #
    def test_resolve_by_host_matches_domain(self):
        """A request host matching a website's Alokai domain resolves to it."""
        Website = self.env['website']
        site = Website.create({
            'name': 'Alokai Host Test',
            'alokai_domain': 'shop.alokai-routing-test.example',
        })
        resolved = Website._alokai_resolve_by_host(
            'https://shop.alokai-routing-test.example/some/path')
        self.assertEqual(resolved, site)

    @mute_logger('odoo.addons.graphql_alokai.models.website')
    def test_resolve_by_host_falls_back(self):
        """An unknown host never breaks a request; it returns a website.

        The fallback warning is expected here (a host was given but matched
        nothing), so it's muted to keep the test log clean.
        """
        Website = self.env['website']
        resolved = Website._alokai_resolve_by_host('no-such-host.invalid')
        self.assertEqual(len(resolved), 1, "fallback should return exactly one website")
