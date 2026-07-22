# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for host -> website resolution (_alokai_resolve_by_host).

Resolution runs on every GraphQL request, so it is cached by host. These tests
pin the behaviour (correct match, normalisation, fallback) and the cache
(repeated resolution issues no query; a Domain change invalidates it because
Website.write() clears the registry caches).
"""

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install', 'alokai_website')
class TestHostResolution(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Website = self.env['website']
        # Two websites with distinct domains so matching (not the single-site
        # fallback) is what selects the result.
        self.w1 = self.Website.search([], limit=1)
        self.w1.domain = 'https://shop-one.example.com'
        self.w2 = self.Website.create({
            'name': 'ZZ Second Site',
            'domain': 'https://shop-two.example.com',
        })

    def test_resolves_by_matching_domain(self):
        self.assertEqual(
            self.Website._alokai_resolve_by_host('shop-one.example.com'), self.w1)
        self.assertEqual(
            self.Website._alokai_resolve_by_host('shop-two.example.com'), self.w2)

    def test_normalises_scheme_path_and_case(self):
        for host in ('https://shop-two.example.com',
                     'shop-two.example.com/',
                     'HTTP://SHOP-TWO.EXAMPLE.COM/blog'):
            self.assertEqual(
                self.Website._alokai_resolve_by_host(host), self.w2,
                "host %r should normalise to shop-two" % host)

    def test_unknown_host_falls_back_to_a_website(self):
        resolved = self.Website._alokai_resolve_by_host('nomatch.example.com')
        self.assertTrue(resolved, "an unmatched host must still resolve to a website")
        self.assertEqual(len(resolved), 1)

    def test_resolution_is_cached(self):
        self.Website._alokai_resolve_by_host('shop-one.example.com')  # warm cache
        q0 = self.env.cr.sql_log_count
        for _ in range(5):
            self.Website._alokai_resolve_by_host('shop-one.example.com')
        self.assertEqual(self.env.cr.sql_log_count, q0,
                         "a cached host resolution must not hit the database")

    def test_cache_invalidated_when_domain_changes(self):
        self.assertEqual(
            self.Website._alokai_resolve_by_host('shop-one.example.com'), self.w1)
        # Repoint w1 to a new domain; Website.write() clears the registry caches.
        self.w1.domain = 'https://shop-moved.example.com'
        self.assertEqual(
            self.Website._alokai_resolve_by_host('shop-moved.example.com'), self.w1,
            "resolution must reflect the new domain, not a stale cache")
