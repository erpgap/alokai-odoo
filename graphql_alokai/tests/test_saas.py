# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Creating the Zeppln instance from Settings, the storefront reporting in
when it starts, and stock and slugs sent to it (Odoo has no Redis there)."""

import hashlib
import hmac
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.website import slug_key

FLAG = 'graphql_alokai.saas_instance_created'
WEBSITE_ID = 'graphql_alokai.saas_website_id'
POST = 'odoo.addons.graphql_alokai.models.website.requests.post'


def dirty_rows(env, kind):
    env.cr.execute("SELECT entry FROM alokai_zeppln_dirty WHERE kind = %s ORDER BY id", [kind])
    return [row[0] for row in env.cr.fetchall()]


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiSaas(HttpCase):

    def setUp(self):
        super().setUp()
        # Zeppln means no Redis in Odoo; and none of this may reach a real one.
        patcher = patch.object(type(self.env['website']), '_redis_enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Start without an instance, whatever the database was used for.
        self.env['website'].search([('alokai_saas_token', '!=', False)]).alokai_saas_token = False

    def _create_instance(self):
        action = self.env['res.config.settings'].create({}).action_create_alokai_instance()
        return parse_qs(urlsplit(action['url']).query)

    def _callback(self, token, payload):
        return self.url_open(
            '/zeppln/instance-ready', json=payload,
            headers={'Authorization': 'Bearer %s' % token} if token else {})

    def test_create_makes_one_website_with_a_token(self):
        params = self._create_instance()
        website = self.env['website'].search([('alokai_saas_token', '!=', False)])
        self.assertEqual(len(website), 1)
        self.assertEqual(website.name, 'Zeppln')
        self.assertEqual(params['token'], [website.alokai_saas_token])
        # Clicking again before the storefront reports in reuses the website.
        self.assertEqual(self._create_instance()['token'], [website.alokai_saas_token])
        self.assertEqual(self.env['website'].search_count([('alokai_saas_token', '!=', False)]), 1)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(FLAG))

    def test_first_call_sets_the_domain_and_sends_everything(self):
        token = self._create_instance()['token'][0]
        response = self._callback(token, {'domain': 'https://shop.zeppln.example/'})
        self.assertEqual(response.json()['status'], 'initialized')
        ICP = self.env['ir.config_parameter'].sudo()
        website = self.env['website'].browse(int(ICP.get_param(WEBSITE_ID)))
        self.assertEqual(website.alokai_domain, 'https://shop.zeppln.example')
        self.assertEqual(website.alokai_saas_token, token, "the token stays, to sign what Odoo sends")
        self.assertEqual(ICP.get_param(FLAG), 'True')
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.alokai_saas_instance_created)
        self.assertEqual(settings.action_open_alokai_instance()['url'], 'https://shop.zeppln.example')

        # Every product and slug is queued for the storefront.
        product_ids = {pid for entry in dirty_rows(self.env, 'stock') for pid in entry['ids']}
        self.assertEqual(product_ids, set(self.env['product.product'].search([]).ids))
        slug_models = {entry['model'] for entry in dirty_rows(self.env, 'slug')}
        self.assertIn('product.template', slug_models)

    def test_later_calls_change_nothing(self):
        """Every restart of the storefront calls again."""
        token = self._create_instance()['token'][0]
        self._callback(token, {'domain': 'https://shop.zeppln.example'})
        rows = len(dirty_rows(self.env, 'stock'))
        response = self._callback(token, {'domain': 'https://other.example'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ignored')
        website = self.env['website'].browse(int(
            self.env['ir.config_parameter'].sudo().get_param(WEBSITE_ID)))
        self.assertEqual(website.alokai_domain, 'https://shop.zeppln.example')
        self.assertEqual(len(dirty_rows(self.env, 'stock')), rows)

    def test_a_bad_token_is_refused(self):
        self._create_instance()
        for token in ('wrong', None):
            self.assertEqual(self._callback(token, {'domain': 'shop.example'}).status_code, 401)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(FLAG))

    def test_a_domain_is_required(self):
        token = self._create_instance()['token'][0]
        self.assertEqual(self._callback(token, {'domain': ''}).status_code, 400)


class _Response:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')


@tagged('post_install', '-at_install', 'alokai')
class TestZepplnSync(TransactionCase):
    """Stock and slugs with Zeppln: queued in Postgres, sent by the dirty cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env['website'].create({
            'name': 'Zeppln', 'alokai_domain': 'shop.zeppln.example',
            'alokai_saas_token': 'zeppln-test-token'})
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_param(WEBSITE_ID, cls.website.id)
        ICP.set_param(FLAG, True)
        cls.product = cls.env['product.product'].create({
            'name': 'Zeppln Stock Test', 'is_storable': True,
            'sale_ok': True, 'is_published': True})
        cls.location = cls.env['stock.warehouse'].search([], limit=1).lot_stock_id

    def setUp(self):
        super().setUp()
        patcher = patch.object(type(self.env['website']), '_redis_enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env.cr.execute("DELETE FROM alokai_zeppln_dirty")
        self.sent = []

    def _post(self, url, data, timeout, headers):
        self.sent.append((url, data.decode(), headers))
        return _Response()

    def _send(self, post=None):
        # The cron commits each batch; a real commit is forbidden in a test.
        with patch.object(self.env.cr, 'commit', lambda *a, **k: None), \
             patch.object(self.env.cr, 'rollback', lambda *a, **k: None), \
             patch(POST, side_effect=post or self._post):
            self.env['product.product']._update_dirty_products_stock_redis()

    def test_a_stock_change_is_queued_in_its_transaction(self):
        self.env['stock.quant'].create({
            'product_id': self.product.id, 'location_id': self.location.id, 'quantity': 7})
        # Seen before any commit: the row is part of the change's transaction.
        self.assertIn(self.product.id, {pid for e in dirty_rows(self.env, 'stock') for pid in e['ids']})

    def test_a_rename_is_queued_with_the_old_slug(self):
        template = self.product.product_tmpl_id
        old = template.website_slug
        template.name = 'Zeppln Renamed'
        entry = dirty_rows(self.env, 'slug')[-1]
        self.assertEqual((entry['model'], entry['ids']), ('product.template', [template.id]))
        self.assertIn(old, entry['previous'])

    def test_the_cron_sends_signed_batches_and_deletes_them(self):
        template = self.product.product_tmpl_id
        old = template.website_slug
        self.env['stock.quant'].create({
            'product_id': self.product.id, 'location_id': self.location.id, 'quantity': 7})
        template.name = 'Zeppln Sent'
        self._send()

        self.assertFalse(dirty_rows(self.env, 'stock') + dirty_rows(self.env, 'slug'))
        sent = {urlsplit(url).path: (json.loads(body), body, headers) for url, body, headers in self.sent}
        stock, _body, _headers = sent['/api/internal/zeppln/stock']
        self.assertEqual(stock['stock'][str(self.product.id)][str(self.website.id)], 7)
        slugs = {s['model']: s for s in sent['/api/internal/zeppln/slugs'][0]['slugs']}
        self.assertIn(slug_key(template.website_slug), slugs['product.template']['set'])
        self.assertIn(slug_key(old), slugs['product.template']['delete'])

        for path, (_payload, body, headers) in sent.items():
            expected = hmac.new(
                b'zeppln-test-token',
                f"POST:{path}:{headers['x-zeppln-timestamp']}:{body}".encode(),
                hashlib.sha256).hexdigest()
            self.assertEqual(headers['x-zeppln-signature'], expected)
        self.assertTrue(all(url.startswith('https://shop.zeppln.example/') for url, *_ in self.sent))

    def test_a_failed_send_keeps_the_rows(self):
        self.env['stock.quant'].create({
            'product_id': self.product.id, 'location_id': self.location.id, 'quantity': 7})

        def unreachable(*args, **kwargs):
            raise requests.ConnectionError('unreachable')

        self._send(unreachable)
        self.assertTrue(dirty_rows(self.env, 'stock'), "kept for the next run")

    def test_full_resyncs_queue_everything(self):
        self.env['product.product']._update_all_products_stock_redis()
        self.env['website']._update_all_slugs_redis()
        product_ids = {pid for e in dirty_rows(self.env, 'stock') for pid in e['ids']}
        self.assertEqual(product_ids, set(self.env['product.product'].search([]).ids))
        self.assertTrue(all(len(e['ids']) <= 1000 for e in dirty_rows(self.env, 'stock')))
        self.assertIn('product.template', {e['model'] for e in dirty_rows(self.env, 'slug')})
