# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Action > Clear Alokai Cache on products and categories.

Requests are captured instead of sent. What is checked is what the
storefront's cacheInvalidationSecurity verifies: the route, the key, and an
HMAC-SHA256 signature of METHOD:PATH:TIMESTAMP:CANONICAL_JSON.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import requests

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

KEY = 'test-invalidation-key'


class _Response:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')


@tagged('post_install', '-at_install')
class TestAlokaiCache(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env['ir.config_parameter'].sudo().set_param('alokai_cache_invalidation_key', KEY)
        cls.website = env['website'].create({
            'name': 'ZZ Shop', 'alokai_domain': 'https://shop.example.com/'})
        cls.other = env['website'].create({'name': 'ZZ Other', 'alokai_domain': 'other.example.com'})
        cls.product = env['product.template'].create({
            'name': 'ZZ Cached Product', 'website_id': cls.website.id})
        cls.category = env['product.public.category'].create({
            'name': 'ZZ Cached Category', 'website_id': cls.website.id})

    def _clear(self, records, status=200):
        sent = []

        def post(session, url, data=None, headers=None, timeout=None):
            sent.append({'url': url, 'body': data.decode(), 'headers': headers})
            return _Response(status)

        with patch.object(requests.Session, 'post', post):
            result = records.action_alokai_clear_cache()
        return sent, result

    def test_product_request_is_signed_as_the_storefront_checks_it(self):
        sent, result = self._clear(self.product)
        self.assertEqual(len(sent), 1)
        request = sent[0]
        route = '/api/internal/cache/invalidate/product'
        self.assertEqual(request['url'], 'https://shop.example.com' + route)
        self.assertEqual(json.loads(request['body']), {'url': self.product.website_slug})
        headers = request['headers']
        self.assertEqual(headers['x-invalidation-key'], KEY)
        expected = hmac.new(
            KEY.encode(),
            f"POST:{route}:{headers['x-invalidation-timestamp']}:{request['body']}".encode(),
            hashlib.sha256).hexdigest()
        self.assertEqual(headers['x-invalidation-signature'], expected)
        self.assertEqual(result['params']['type'], 'success')

    def test_a_separate_signing_secret_signs_instead_of_the_key(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'alokai_cache_invalidation_signing_secret', 'separate-secret')
        sent, _result = self._clear(self.product)
        request = sent[0]
        headers = request['headers']
        self.assertEqual(headers['x-invalidation-key'], KEY)
        expected = hmac.new(
            b'separate-secret',
            f"POST:/api/internal/cache/invalidate/product:"
            f"{headers['x-invalidation-timestamp']}:{request['body']}".encode(),
            hashlib.sha256).hexdigest()
        self.assertEqual(headers['x-invalidation-signature'], expected)

    def test_category_uses_the_category_route(self):
        sent, _result = self._clear(self.category)
        self.assertEqual(
            [r['url'] for r in sent],
            ['https://shop.example.com/api/internal/cache/invalidate/category'])
        self.assertEqual(json.loads(sent[0]['body']), {'url': self.category.website_slug})

    def test_a_record_on_every_website_clears_each_storefront(self):
        """A product without a website shows on all of them; a domain
        without a scheme is taken as https."""
        (self.env['website'].search([]) - self.other).write(
            {'alokai_domain': 'https://shop.example.com'})
        self.product.website_id = False
        sent, _result = self._clear(self.product)
        bases = {r['url'].split('/api/')[0] for r in sent}
        self.assertEqual(bases, {'https://shop.example.com', 'https://other.example.com'})

    def test_several_records_in_one_action(self):
        second = self.env['product.template'].create({
            'name': 'ZZ Second Product', 'website_id': self.website.id})
        sent, result = self._clear(self.product + second)
        self.assertEqual(
            sorted(json.loads(r['body'])['url'] for r in sent),
            sorted([self.product.website_slug, second.website_slug]))
        self.assertIn('2', result['params']['message'])

    def test_without_the_key_it_is_an_error(self):
        self.env['ir.config_parameter'].sudo().set_param('alokai_cache_invalidation_key', '')
        with self.assertRaises(UserError):
            self._clear(self.product)

    def test_a_website_without_alokai_domain_is_an_error(self):
        self.website.alokai_domain = False
        with self.assertRaises(UserError):
            self._clear(self.product)

    def test_a_refused_request_is_reported(self):
        sent, result = self._clear(self.product, status=401)
        self.assertEqual(len(sent), 1)
        self.assertEqual(result['params']['type'], 'warning')
        self.assertIn('401', result['params']['message'])
