# -*- coding: utf-8 -*-
from odoo import api, models
from odoo.http import request


class ProductWishlist(models.Model):
    _inherit = 'product.wishlist'

    def current(self):
        """Show a customer's wishlist on every website that sells the product.

        A row is unique per (product, partner) across all websites, so a product can be
        wishlisted once: listing only the current website's rows hid a product sold on several
        websites from the others, and adding it there failed with a duplicate key error.
        Products the current website doesn't sell stay hidden by the filter below.
        """
        if not request or request.website.is_public_user():
            return super().current()
        wish = self.search([('partner_id', '=', self.env.user.partner_id.id)])
        return wish.filtered(
            lambda wish:
                wish.sudo().product_id.product_tmpl_id.website_published
                and wish.sudo().product_id.product_tmpl_id._is_add_to_cart_possible()
        )

    @api.model
    def _add_to_wishlist(self, pricelist_id, currency_id, website_id, price, product_id, partner_id=False):
        """Reuse the customer's row for this product instead of creating a second one, which
        the (product, partner) unique constraint refuses: reactivate it if it was archived."""
        if partner_id:
            existing = self.sudo().with_context(active_test=False).search([
                ('partner_id', '=', partner_id),
                ('product_id', '=', product_id),
            ], limit=1)
            if existing:
                if not existing.active:
                    existing.active = True
                return existing.with_env(self.env)
        return super()._add_to_wishlist(pricelist_id, currency_id, website_id, price, product_id, partner_id)
