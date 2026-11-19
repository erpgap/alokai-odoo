# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import base64

from odoo import tools
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWebsiteLogo(TransactionCase):
    """The storefront shows the website's logo: the Alokai wordmark until the
    merchant sets their own."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with tools.file_open('graphql_alokai/static/img/alokai_wordmark.svg', 'rb') as f:
            cls.wordmark = base64.b64encode(f.read())
        with tools.file_open('website/static/src/img/website_logo.svg', 'rb') as f:
            cls.placeholder = base64.b64encode(f.read())

    def test_a_new_website_has_the_wordmark(self):
        website = self.env['website'].create({'name': 'Logo test'})
        self.assertEqual(website.logo, self.wordmark)

    def test_the_placeholder_is_replaced_and_a_merchant_logo_kept(self):
        on_placeholder = self.env['website'].create({'name': 'Placeholder', 'logo': self.placeholder})
        own = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"/>')
        with_own = self.env['website'].create({'name': 'Own logo', 'logo': own})
        self.env['website']._alokai_replace_placeholder_logos()
        self.assertEqual(on_placeholder.logo, self.wordmark)
        self.assertEqual(with_own.logo, own)
