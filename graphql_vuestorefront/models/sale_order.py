# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
from odoo import models

class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_preview_sale_order(self):
        action = super().action_preview_sale_order()
        action['url'] = self.get_portal_url()
        return action

    def _update_sale_order(self, website, user):
        """ This function is used to force some necessary updates on the SO """
        # SO Updates
        order_vals = {}
        partner = user.partner_id
        if not self.partner_id.is_public_user:
            partner = self.partner_id
        else:
            partner = user.partner_id.commercial_partner_id

        delivery_addr = partner.address_get(['delivery'])
        invoice_addr = partner.address_get(['invoice'])
        # Update Pricelist, Partner and respective Addresses
        pricelist = partner.property_product_pricelist.id or website.pricelist_id.id

        order_vals.update({
            'pricelist_id': pricelist,
            'partner_id': partner.id,
            'partner_shipping_id': delivery_addr['delivery'],
            'partner_invoice_id': invoice_addr['invoice'],
        })
        # Update Payment Term
        payment_term_id = partner.property_payment_term_id
        if self.payment_term_id.id != payment_term_id.id:
            order_vals.update({'payment_term_id': payment_term_id.id})

        # Update SO
        self.write(order_vals)

        # Force correct fiscal position and prices
        self._compute_fiscal_position_id()
        self.order_line._compute_tax_id()
        self._recompute_prices()