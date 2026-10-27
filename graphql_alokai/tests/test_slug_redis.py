# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the slug keys website.slug.redis.mixin keeps in Redis.

The storefront answers 404 for any URL with no key, without asking Odoo, so a
record the storefront should route to needs its key and one it should not -
unpublished, archived, renamed, deleted - must lose it. The mixin used to only
ever add keys, leaving the old ones until the nightly resync.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from ..models.website import slug_key
from .test_stock import _FakeRedis, run_dirty_sync


@tagged('post_install', '-at_install', 'alokai')
class TestSlugRedis(TransactionCase):

    def setUp(self):
        super().setUp()
        website_cls = type(self.env['website'])
        # Drop flags queued by earlier tests, with Redis off.
        with patch.object(website_cls, '_redis_enabled', return_value=False):
            self.env.cr.postcommit.run()
        self.redis = _FakeRedis()
        for name, value in (('_redis_enabled', True), ('_redis_connect', self.redis)):
            patcher = patch.object(website_cls, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _product(self, name='Slug Test Product'):
        return self.env['product.template'].create({
            'name': name, 'sale_ok': True, 'is_published': True})

    def _store(self):
        """Redis once saves so far have been synced by the dirty cron."""
        run_dirty_sync(self.env)
        return self.redis.store

    def _has_key(self, record):
        return slug_key(record.website_slug) in self._store()

    def test_a_published_product_has_a_key(self):
        product = self._product()
        self.assertEqual(
            self._store()[slug_key(product.website_slug)], 'product.template')

    def test_unpublishing_removes_the_key(self):
        product = self._product()
        product.is_published = False
        self.assertFalse(self._has_key(product))

    def test_archiving_removes_the_key(self):
        product = self._product()
        product.active = False
        self.assertFalse(self._has_key(product))

    def test_no_longer_for_sale_removes_the_key(self):
        product = self._product()
        product.sale_ok = False
        self.assertFalse(self._has_key(product))

    def test_renaming_moves_the_key(self):
        product = self._product('Old Name')
        old = product.website_slug
        product.name = 'New Name'
        self.assertNotEqual(product.website_slug, old)
        self.assertNotIn(slug_key(old), self._store())
        self.assertTrue(self._has_key(product))

    def test_deleting_removes_the_key(self):
        product = self._product()
        key = slug_key(product.website_slug)
        product.unlink()
        self.assertNotIn(key, self._store())

    def test_a_key_another_model_owns_is_left_alone(self):
        product = self._product()
        self._store()[slug_key(product.website_slug)] = 'alokai.website.page'
        product.is_published = False
        self.assertEqual(
            self._store()[slug_key(product.website_slug)], 'alokai.website.page')

    def test_a_category_loses_its_key_when_deleted(self):
        category = self.env['product.public.category'].create({'name': 'Slug Test Cat'})
        key = slug_key(category.website_slug)
        self.assertEqual(self._store().get(key), 'product.public.category')
        category.unlink()
        self.assertNotIn(key, self._store())

    def test_nothing_is_read_with_redis_off(self):
        product = self._product()
        website_cls = type(self.env['website'])
        with patch.object(website_cls, '_redis_enabled', return_value=False), \
             patch.object(website_cls, '_redis_connect', return_value=None), \
             patch.object(type(product), '_redis_slugs') as slugs:
            product.name = 'Renamed While Off'
        slugs.assert_not_called()

    def test_a_save_reaches_redis_only_through_the_cron(self):
        """Not during the save: a save rolled back after it would leave keys
        for a state that never existed."""
        product = self._product()
        self.assertNotIn(slug_key(product.website_slug), self.redis.store)
        self.assertTrue(self._has_key(product))
        self.assertFalse([k for k in self._store() if k.startswith('dirty:')],
                         "the cron consumes its flags")

    def test_full_resync_keeps_existing_keys(self):
        """The full resync marks every slug dirty rather than wiping them
        first, which would 404 every page until the cron caught up."""
        product = self._product()
        self._store()
        self.env['website']._update_all_slugs_redis()
        self.assertTrue(self._has_key(product))
