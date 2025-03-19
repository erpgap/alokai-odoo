# -*- coding: utf-8 -*-

from odoo import models, fields, api, _


class AlokaiMixin(models.AbstractModel):
    _name = "alokai.mixin"
    _description = 'Alokai GraphQL Mixin'

    _exclude_fields = "create_uid,write_uid,create_date,write_date"

    def init(self):
        print(f"init {self._name}")

class FleetVehicle(models.Model):
    _name = 'fleet.vehicle'
    _inherit = ['fleet.vehicle', 'alokai.mixin']
    _exclude_fields = "create_uid,create_date"
