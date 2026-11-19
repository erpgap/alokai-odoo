# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
from odoo.tests.common import HttpCase, tagged

from odoo.addons.graphql_alokai.schemas.objects import get_image_filename

# A 1x1 PNG.
PIXEL = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='


@tagged('post_install', '-at_install')
class TestImageCache(HttpCase):
    """Catalogue images on their current URL are kept 30 days; an outdated
    URL is not, so a changed image is never held on to."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.template'].create({
            'name': 'Cache Test Bag', 'image_1920': PIXEL, 'is_published': True,
        })

    def _get(self, filename):
        return self.url_open(f'/web/image/product.template/{self.product.id}/image_1920/64x64/{filename}')

    def test_the_current_url_is_kept_30_days(self):
        response = self._get(get_image_filename(self.product))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'public, max-age=2592000')

    def test_an_outdated_url_keeps_odoo_caching(self):
        response = self._get('1a2b3c_cache-test-bag')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('max-age=2592000', response.headers.get('Cache-Control', ''))
