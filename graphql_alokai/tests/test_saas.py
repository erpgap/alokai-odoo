# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Creating the Alokai instance from Settings and the SaaS callback that
reports its domain back."""

from urllib.parse import parse_qs, urlsplit

from odoo.tests import HttpCase, tagged

FLAG = 'graphql_alokai.saas_instance_created'


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiSaas(HttpCase):

    def _create_instance(self):
        action = self.env['res.config.settings'].create({}).action_create_alokai_instance()
        return parse_qs(urlsplit(action['url']).query)

    def _callback(self, token, payload):
        return self.url_open(
            '/alokai/saas/instance', json=payload,
            headers={'Authorization': 'Bearer %s' % token} if token else {})

    def test_create_makes_one_website_with_a_token(self):
        params = self._create_instance()
        website = self.env['website'].search([('alokai_saas_token', '!=', False)])
        self.assertEqual(len(website), 1)
        self.assertEqual(params['token'], [website.alokai_saas_token])
        self.assertTrue(params['callback'][0].endswith('/alokai/saas/instance'))
        # Clicking again before the callback reuses the pending website.
        self.assertEqual(self._create_instance()['token'], [website.alokai_saas_token])
        self.assertEqual(self.env['website'].search_count([('alokai_saas_token', '!=', False)]), 1)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(FLAG))

    def test_callback_sets_domain_and_flag(self):
        token = self._create_instance()['token'][0]
        response = self._callback(token, {'domain': 'https://shop.zeppln.example/'})
        self.assertEqual(response.status_code, 200)
        website = self.env['website'].browse(int(
            self.env['ir.config_parameter'].sudo().get_param('graphql_alokai.saas_website_id')))
        self.assertEqual(website.name, 'Zeppln')
        self.assertEqual(website.alokai_domain, 'https://shop.zeppln.example')
        self.assertFalse(website.alokai_saas_token)
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_param(FLAG), 'True')
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.alokai_saas_instance_created)
        self.assertEqual(settings.action_open_alokai_instance()['url'], 'https://shop.zeppln.example')
        # The token is spent: replaying the request is refused.
        self.assertEqual(self._callback(token, {'domain': 'evil.example'}).status_code, 401)
        self.assertEqual(website.alokai_domain, 'https://shop.zeppln.example')

    def test_callback_rejects_a_bad_token(self):
        self._create_instance()
        for token in ('wrong', None):
            self.assertEqual(self._callback(token, {'domain': 'shop.example'}).status_code, 401)
        self.assertFalse(self.env['ir.config_parameter'].sudo().get_param(FLAG))

    def test_callback_requires_a_domain(self):
        token = self._create_instance()['token'][0]
        self.assertEqual(self._callback(token, {'domain': ''}).status_code, 400)
