# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the immediate Redis stock refresh on reservation.

A reservation (a sale dropping free_qty) should run the dirty-stock cron ASAP
instead of waiting for its 1-minute tick, so the storefront's Redis stock stays
fresh. These tests don't require Redis: the redis gate and the cron trigger are
patched.
"""

from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

STOCK_QUANT = 'odoo.addons.graphql_alokai.models.stock.StockQuant'


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

    def test_reservation_triggers_immediate_sync(self):
        """Writing reserved_quantity triggers the immediate cron path."""
        cls = type(self.env['stock.quant'])
        with patch.object(cls, '_trigger_dirty_stock_cron') as trigger:
            self.quant.write({'reserved_quantity': 2.0})
        trigger.assert_called_once()

    def test_non_reservation_change_does_not_trigger(self):
        """A plain quantity change (receipt, inventory) uses the normal tick."""
        cls = type(self.env['stock.quant'])
        with patch.object(cls, '_trigger_dirty_stock_cron') as trigger:
            self.quant.write({'quantity': 20.0})
        trigger.assert_not_called()

    def _dirty_cron_trigger_count(self):
        return self.env['ir.cron.trigger'].search_count(
            [('cron_id', '=', self.dirty_cron.id)])

    def test_trigger_noop_when_redis_disabled(self):
        """With Redis off, no cron trigger is scheduled."""
        before = self._dirty_cron_trigger_count()
        website_cls = type(self.env['website'])
        with patch.object(website_cls, '_redis_enabled', return_value=False):
            self.env['stock.quant']._trigger_dirty_stock_cron()
            self.env.cr.postcommit.run()
        self.assertEqual(self._dirty_cron_trigger_count(), before)

    def test_trigger_schedules_cron_when_redis_enabled(self):
        """With Redis on, the dirty-stock cron is triggered ASAP."""
        before = self._dirty_cron_trigger_count()
        website_cls = type(self.env['website'])
        with patch.object(website_cls, '_redis_enabled', return_value=True):
            self.env['stock.quant']._trigger_dirty_stock_cron()
            self.env.cr.postcommit.run()
        self.assertEqual(self._dirty_cron_trigger_count(), before + 1)
