# -*- coding: utf-8 -*-
# Copyright 2025 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import tools, models, api, _


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _create_stock_is_dirty_redis(self):
        # In some situations, like running tests, skip redis
        if self.env['ir.config_parameter'].sudo().get_param('alokai_disable_redis_stock', False):
            return 0

        redis_client = self.env['website']._redis_connect()
        try:
            pipe = redis_client.pipeline()

            for quant in self:
                product_id = quant.product_id.id
                product_key = f'stock:product-is-dirty-{product_id}'
                pipe.set(product_key, product_id)

            pipe.execute()
        finally:
            redis_client.close()

    def write(self, vals):
        res = super(StockQuant, self).write(vals)
        quantity_fields = {'quantity', 'reserved_quantity', 'inventory_quantity'}
        if quantity_fields.intersection(vals.keys()):
            self._create_stock_is_dirty_redis()
        return res

    @api.model_create_multi
    def create(self, vals_list):
        quants = super(StockQuant, self).create(vals_list)
        quants._create_stock_is_dirty_redis()
        return quants

    def unlink(self):
        self._create_stock_is_dirty_redis()
        return super(StockQuant, self).unlink()
