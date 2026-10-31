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

from odoo.exceptions import UserError
from odoo.tests import HttpCase, TransactionCase, tagged

from ..models.website import slug_key

POST = 'odoo.addons.graphql_alokai.models.website.requests.post'
LAUNCH = 'odoo.addons.graphql_alokai.models.res_config_settings.requests.post'


def dirty_rows(env, kind):
    env.cr.execute("SELECT entry FROM alokai_sync_queue WHERE kind = %s ORDER BY id", [kind])
    return [row[0] for row in env.cr.fetchall()]


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiSaas(HttpCase):

    def setUp(self):
        super().setUp()
        # Pushing to storefronts means no Redis in Odoo; and none of this may
        # reach a real one.
        patcher = patch.object(type(self.env['website']), '_redis_enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Start without an instance, whatever the database was used for.
        self.env['website'].search([('alokai_saas_token', '!=', False)]).alokai_saas_token = False

    def _create_instance(self, answer=None):
        """Settings' button, with Zeppln answering `answer` (by default a
        redirect to its signup page, naming another host). Returns the new
        website, the URL opened for the user and what Zeppln was sent."""
        calls = []

        def zeppln(url, data, **kwargs):
            calls.append((url, data))
            if isinstance(answer, Exception):
                raise answer
            return answer or _Response(303, {'Location': 'https://elsewhere.example/signup?session=abc'})

        with patch(LAUNCH, side_effect=zeppln):
            action = self.env['res.config.settings'].create({}).action_create_alokai_instance()
        website = self.env['website'].search([('alokai_saas_token', '!=', False)], order='id desc', limit=1)
        return website, action['url'], calls

    def _callback(self, token, payload):
        return self.url_open(
            '/zeppln/instance-ready', json=payload,
            headers={'Authorization': 'Bearer %s' % token} if token else {})

    def test_create_makes_a_website_and_launches_it(self):
        before = self.env['website'].search_count([])
        first, url, calls = self._create_instance()
        self.assertEqual(self.env['website'].search_count([]), before + 1)

        # The token went to Zeppln server to server, never into a URL.
        (target, data), = calls
        self.assertEqual(target, 'https://www.zeppln.com/instance/launch')
        self.assertEqual(data['odooToken'], first.alokai_saas_token)
        self.assertNotIn(first.alokai_saas_token, url)
        # Zeppln's page, on Zeppln's host whatever host its answer named.
        self.assertEqual(url, 'https://www.zeppln.com/signup?session=abc')
        self.assertFalse(first.alokai_zeppln_instance, "not until Zeppln reports its URL")
        self.assertFalse(self.env['website']._push_websites())

    def test_create_again_relaunches_the_website_still_waiting(self):
        """Clicking again before the instance reports in retries its launch:
        five clicks are not five websites."""
        before = self.env['website'].search_count([])
        first, _url, _calls = self._create_instance()
        token = first.alokai_saas_token
        again, _url, calls = self._create_instance()
        self.assertEqual(again, first)
        self.assertEqual(again.alokai_saas_token, token)
        self.assertEqual(calls[0][1]['odooToken'], token)
        self.assertEqual(self.env['website'].search_count([]), before + 1)

        # Once it has reported in, the next click is a new instance.
        self._callback(token, {'instance_url': 'https://shop.zeppln.example'})
        second, _url, _calls = self._create_instance()
        self.assertNotEqual(second, first)
        self.assertNotEqual(second.alokai_saas_token, token)
        self.assertNotEqual(second.name, first.name)
        self.assertEqual(self.env['website'].search_count([]), before + 2)

    def test_zeppln_unreachable_or_refusing_creates_nothing(self):
        before = self.env['website'].search_count([])
        for answer in (requests.ConnectionError('down'), _Response(500)):
            # A request that raises rolls back; the savepoint stands in for it.
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self._create_instance(answer)
        self.assertEqual(self.env['website'].search_count([]), before)

    def test_the_callback_sets_the_url_and_queues_everything(self):
        website, _url, _calls = self._create_instance()
        token = website.alokai_saas_token
        response = self._callback(token, {'instance_url': 'https://shop.zeppln.example/'})
        self.assertEqual(response.json()['status'], 'initialized')
        website.invalidate_recordset()
        self.assertEqual(website.alokai_domain, 'https://shop.zeppln.example')
        self.assertEqual(website.alokai_saas_token, token, "the token stays, to sign what Odoo sends")
        self.assertTrue(website.alokai_zeppln_instance)
        self.assertEqual(website.action_open_zeppln_instance()['url'], 'https://shop.zeppln.example')
        # From now on stock and slugs go to this storefront.
        Website = self.env['website']
        self.assertEqual(Website._push_websites(), website)
        self.assertEqual(Website._alokai_sync_mode(), 'push')

        # Every product and slug is queued, for the cron to send once it is up.
        product_ids = {pid for entry in dirty_rows(self.env, 'stock') for pid in entry['ids']}
        self.assertEqual(product_ids, set(self.env['product.product'].search([]).ids))
        slug_models = {entry['model'] for entry in dirty_rows(self.env, 'slug')}
        self.assertIn('product.template', slug_models)

    def test_the_same_url_again_changes_nothing(self):
        website, _url, _calls = self._create_instance()
        token = website.alokai_saas_token
        self._callback(token, {'instance_url': 'https://shop.zeppln.example'})
        rows = len(dirty_rows(self.env, 'stock'))
        response = self._callback(token, {'instance_url': 'https://shop.zeppln.example'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'unchanged')
        self.assertEqual(len(dirty_rows(self.env, 'stock')), rows)

    def test_a_new_url_replaces_the_old_one(self):
        """An instance moved to another URL is sent everything again."""
        website, _url, _calls = self._create_instance()
        token = website.alokai_saas_token
        self._callback(token, {'instance_url': 'https://shop.zeppln.example'})
        response = self._callback(token, {'instance_url': 'https://other.example'})
        self.assertEqual(response.json()['status'], 'initialized')
        website.invalidate_recordset()
        self.assertEqual(website.alokai_domain, 'https://other.example')

    def test_the_callback_finds_its_own_website(self):
        first, _url, _calls = self._create_instance()
        self._callback(first.alokai_saas_token, {'instance_url': 'https://one.zeppln.example'})
        second, _url, _calls = self._create_instance()
        self._callback(second.alokai_saas_token, {'instance_url': 'https://two.zeppln.example'})
        (first | second).invalidate_recordset()
        self.assertEqual(first.alokai_domain, 'https://one.zeppln.example')
        self.assertEqual(second.alokai_domain, 'https://two.zeppln.example')

    def test_a_bad_token_is_refused(self):
        self._create_instance()
        for token in ('wrong', None):
            self.assertEqual(self._callback(token, {'instance_url': 'shop.example'}).status_code, 401)
        self.assertFalse(self.env['website']._push_websites())

    def test_a_url_is_required(self):
        website, _url, _calls = self._create_instance()
        self.assertEqual(self._callback(website.alokai_saas_token, {'instance_url': ''}).status_code, 400)

    def test_open_needs_a_reported_instance(self):
        website, _url, _calls = self._create_instance()
        with self.assertRaises(UserError):
            website.action_open_zeppln_instance()


class _Response:
    def __init__(self, status=200, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Client Error')


@tagged('post_install', '-at_install', 'alokai')
class TestPushSync(TransactionCase):
    """Stock and slugs for storefronts Odoo pushes to (Zeppln's): queued in
    Postgres, sent by the dirty cron."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A website with a token and a reported domain is one Odoo pushes to:
        # nothing else switches pushing on (the callback used to set config
        # parameters that these tests set by hand, so they kept passing after
        # it stopped).
        cls.env['website'].search([('alokai_saas_token', '!=', False)]).alokai_saas_token = False
        cls.website = cls.env['website'].create({
            'name': 'Zeppln', 'alokai_domain': 'shop.zeppln.example',
            'alokai_saas_token': 'zeppln-test-token'})
        cls.product = cls.env['product.product'].create({
            'name': 'Zeppln Stock Test', 'is_storable': True,
            'sale_ok': True, 'is_published': True})
        cls.location = cls.env['stock.warehouse'].search([], limit=1).lot_stock_id

    def setUp(self):
        super().setUp()
        patcher = patch.object(type(self.env['website']), '_redis_enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.env.cr.execute("DELETE FROM alokai_sync_queue")
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

    def test_every_storefront_gets_each_batch(self):
        self.env['website'].create({
            'name': 'Zeppln 2', 'alokai_domain': 'two.zeppln.example',
            'alokai_saas_token': 'second-token'})
        self.env['stock.quant'].create({
            'product_id': self.product.id, 'location_id': self.location.id, 'quantity': 7})
        self._send()
        hosts = {urlsplit(url).netloc for url, *_ in self.sent}
        self.assertEqual(hosts, {'shop.zeppln.example', 'two.zeppln.example'})
        self.assertFalse(dirty_rows(self.env, 'stock'))

    def test_one_storefront_down_keeps_the_batch_for_all(self):
        self.env['website'].create({
            'name': 'Zeppln 2', 'alokai_domain': 'two.zeppln.example',
            'alokai_saas_token': 'second-token'})
        self.env['stock.quant'].create({
            'product_id': self.product.id, 'location_id': self.location.id, 'quantity': 7})

        def second_down(url, data, timeout, headers):
            if 'two.zeppln.example' in url:
                raise requests.ConnectionError('not up yet')
            return self._post(url, data, timeout, headers)

        self._send(second_down)
        self.assertTrue(dirty_rows(self.env, 'stock'), "kept, and sent to both on the next run")
