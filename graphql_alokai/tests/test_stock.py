# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the immediate Redis stock refresh on reservation.

A reservation (a sale dropping free_qty) should run the dirty-stock cron ASAP
instead of waiting for its 1-minute tick, so the storefront's Redis stock stays
fresh. These tests don't require Redis: the redis gate and the cron trigger are
patched.
"""

import fnmatch

from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

STOCK_QUANT = 'odoo.addons.graphql_alokai.models.stock.StockQuant'


class _FakePipeline:
    def __init__(self, store, log):
        self._store = store
        self._log = log
        self._ops = []

    def set(self, key, value):
        self._ops.append((key, value))

    def execute(self):
        for key, value in self._ops:
            self._store[key] = value
        self._ops = []
        self._log.append('redis')


class _FakeRedis:
    """Minimal in-memory Redis stand-in for the stock sync."""

    def __init__(self, log=None):
        self.store = {}
        self.log = log if log is not None else []

    def pipeline(self):
        return _FakePipeline(self.store, self.log)

    def set(self, key, value):
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def scan_iter(self, match):
        return [k for k in list(self.store) if fnmatch.fnmatch(k, match)]

    def delete(self, key):
        self.store.pop(key, None)

    def close(self):
        pass


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiStockTrigger(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        product = cls.env['product.product'].search([('is_storable', '=', True)], limit=1)
        if not product:
            product = cls.env['product.product'].create(
                {'name': 'Alokai Stock Test', 'is_storable': True})
        cls.product = product
        cls.location = cls.env['stock.warehouse'].search([], limit=1).lot_stock_id
        cls.quant = cls.env['stock.quant'].create({
            'product_id': cls.product.id,
            'location_id': cls.location.id,
            'quantity': 10.0,
        })
        cls.dirty_cron = cls.env.ref(
            'graphql_alokai.ir_cron_update_dirty_products_stock_redis')

    def setUp(self):
        super().setUp()
        # Drain any postcommit callbacks queued earlier so each test is
        # isolated; force Redis off so the drain never touches a real server.
        with patch.object(type(self.env['website']), '_redis_enabled', return_value=False):
            self.env.cr.postcommit.run()

    def test_redis_stock_lookup_indexes_exist(self):
        """The (website_id, quantity, product_id) index backing the in-stock
        lookup must be created on both redis-stock tables (init hook)."""
        self.env.cr.execute("""
            SELECT indexname FROM pg_indexes
            WHERE indexname IN (
                'product_template_redis_stock_website_qty_idx',
                'product_product_redis_stock_website_qty_idx')
        """)
        found = {row[0] for row in self.env.cr.fetchall()}
        self.assertEqual(found, {
            'product_template_redis_stock_website_qty_idx',
            'product_product_redis_stock_website_qty_idx',
        }, "both redis-stock lookup indexes must exist")

    def test_reservation_triggers_immediate_sync(self):
        """Writing reserved_quantity triggers the immediate cron path."""
        cls = type(self.env['stock.quant'])
        with patch.object(cls, '_trigger_dirty_stock_cron') as trigger:
            self.quant.write({'reserved_quantity': 2.0})
        trigger.assert_called_once()

    def test_quantity_change_triggers_immediate_sync(self):
        """A plain quantity change (receipt, inventory) also syncs ASAP."""
        cls = type(self.env['stock.quant'])
        with patch.object(cls, '_trigger_dirty_stock_cron') as trigger:
            self.quant.write({'quantity': 20.0})
        trigger.assert_called_once()

    def _dirty_cron_trigger_count(self):
        return self.env['ir.cron.trigger'].search_count(
            [('cron_id', '=', self.dirty_cron.id)])

    def test_trigger_noop_when_redis_disabled(self):
        """With Redis off, no cron trigger is scheduled."""
        before = self._dirty_cron_trigger_count()
        website_cls = type(self.env['website'])
        with patch.object(website_cls, '_redis_enabled', return_value=False):
            self.env['stock.quant']._trigger_dirty_stock_cron()
        self.assertEqual(self._dirty_cron_trigger_count(), before)

    def test_trigger_schedules_cron_when_redis_enabled(self):
        """With Redis on, the dirty-stock cron is triggered ASAP.

        The trigger must be created inline, within the writing transaction:
        a row created in a postcommit callback runs after the final COMMIT
        and is rolled back when the request cursor closes.
        """
        # _trigger silently skips inactive crons; make the test independent
        # of the database's cron state (rolled back with the transaction).
        self.dirty_cron.sudo().active = True
        before = self._dirty_cron_trigger_count()
        website_cls = type(self.env['website'])
        with patch.object(website_cls, '_redis_enabled', return_value=True):
            self.env['stock.quant']._trigger_dirty_stock_cron()
        self.assertEqual(self._dirty_cron_trigger_count(), before + 1)

    # ------------------------------------------------------------------ #
    #  dirty-stock cron sync (fake Redis)                                 #
    # ------------------------------------------------------------------ #
    def _dirty_key(self):
        return f'stock:product-is-dirty-{self.product.id}-x'

    def test_cron_syncs_dirty_product(self):
        """The cron syncs a flagged product and consumes its flag."""
        fake = _FakeRedis()
        fake.store[self._dirty_key()] = str(self.product.id)
        website_cls = type(self.env['website'])
        # The cron commits (real commit is forbidden inside a test); stub it.
        with patch.object(self.env.cr, 'commit', lambda *a, **k: None), \
             patch.object(website_cls, '_redis_enabled', return_value=True), \
             patch.object(website_cls, '_redis_connect', return_value=fake):
            self.env['product.product']._update_dirty_products_stock_redis()

        self.assertNotIn(self._dirty_key(), fake.store, "flag should be consumed")
        self.assertIn(f'stock:product-{self.product.id}', fake.store,
                      "secondary Redis JSON should be written")
        row = self.env['product.product.redis_stock'].search(
            [('product_id', '=', self.product.id)])
        self.assertTrue(row, "authoritative Postgres row should be written")

    def test_cron_writes_redis_before_postgres(self):
        """Redis (the store the website reads) is written before Postgres.

        Redis stock is critical for the storefront; the Postgres redis_stock
        tables are secondary (visualization/filtering). Writing Redis first
        means a Postgres failure can never leave the website without the fresh
        stock, and the surviving dirty flags retry Postgres on the next run.
        """
        log = []
        fake = _FakeRedis(log=log)
        fake.store[self._dirty_key()] = str(self.product.id)
        website_cls = type(self.env['website'])
        pp_redis = type(self.env['product.product.redis_stock'])
        orig_bulk = pp_redis.bulk_update_redis_stock

        def logged_bulk(rec_self, values):
            log.append('pg')
            return orig_bulk(rec_self, values)

        with patch.object(self.env.cr, 'commit', lambda *a, **k: None), \
             patch.object(website_cls, '_redis_enabled', return_value=True), \
             patch.object(website_cls, '_redis_connect', return_value=fake), \
             patch.object(pp_redis, 'bulk_update_redis_stock', logged_bulk), \
             patch.object(type(self.env['product.template.redis_stock']),
                          'bulk_update_redis_stock', logged_bulk):
            self.env['product.product']._update_dirty_products_stock_redis()

        self.assertIn('pg', log)
        self.assertIn('redis', log)
        self.assertLess(log.index('redis'), log.index('pg'),
                        "Redis (critical for the website) must be written first")

    def test_cron_keeps_flags_on_failure(self):
        """If the update raises, the dirty flags survive for the next run."""
        fake = _FakeRedis()
        fake.store[self._dirty_key()] = str(self.product.id)
        website_cls = type(self.env['website'])
        product_cls = type(self.env['product.product'])

        def failing_update(rec_self, redis_client):
            raise ValueError("boom")

        with patch.object(self.env.cr, 'commit', lambda *a, **k: None), \
             patch.object(website_cls, '_redis_enabled', return_value=True), \
             patch.object(website_cls, '_redis_connect', return_value=fake), \
             patch.object(product_cls, '_update_products_stock_redis', failing_update):
            with self.assertRaises(ValueError):
                self.env['product.product']._update_dirty_products_stock_redis()

        self.assertIn(self._dirty_key(), fake.store,
                      "flags must survive a failed update to be retried")

    def test_cron_resets_snapshot_before_compute(self):
        """The cron commits (resets its snapshot) before reading free_qty."""
        order = []
        fake = _FakeRedis()
        fake.store[self._dirty_key()] = str(self.product.id)
        website_cls = type(self.env['website'])
        product_cls = type(self.env['product.product'])
        orig_compute = product_cls._update_products_stock_redis

        def logged_commit(*args, **kwargs):
            # Record only; a real commit is forbidden inside a test.
            order.append('commit')

        def logged_compute(rec_self, redis_client):
            order.append('compute')
            return orig_compute(rec_self, redis_client)

        with patch.object(website_cls, '_redis_enabled', return_value=True), \
             patch.object(website_cls, '_redis_connect', return_value=fake), \
             patch.object(self.env.cr, 'commit', logged_commit), \
             patch.object(product_cls, '_update_products_stock_redis', logged_compute):
            self.env['product.product']._update_dirty_products_stock_redis()

        self.assertIn('commit', order)
        self.assertIn('compute', order)
        self.assertLess(order.index('commit'), order.index('compute'),
                        "snapshot must be reset before computing free_qty")
