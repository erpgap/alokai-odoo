# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Correctness test for the UpdateAddress mutation.

The mutation used to recompute the order's fiscal position (and therefore the
taxes) *before* writing the new address, so changing the shipping country left
the order on the old country's fiscal position. This pins the fixed behaviour:
the fiscal position follows the newly-saved address.
"""

from odoo.tests.common import TransactionCase, tagged, new_test_user

from odoo.addons.website_sale.tests.common import MockRequest
from odoo.addons.graphql_alokai.schemas.address import UpdateAddress


class _Info:
    def __init__(self, env):
        self.context = {'env': env}


@tagged('post_install', '-at_install', 'alokai_address')
class TestUpdateAddressFiscalPosition(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.website = env['website'].get_current_website()

        # Obscure countries so our auto-applied fiscal positions are the only
        # ones that can match (no clash with demo/localization data).
        cls.country_a = env.ref('base.tv')   # Tuvalu
        cls.country_b = env.ref('base.nr')   # Nauru
        cls.fp_a = env['account.fiscal.position'].create({
            'name': 'ZZ FP A', 'auto_apply': True, 'country_id': cls.country_a.id,
        })
        cls.fp_b = env['account.fiscal.position'].create({
            'name': 'ZZ FP B', 'auto_apply': True, 'country_id': cls.country_b.id,
        })

        # Portal user whose commercial partner owns the shipping address, so
        # get_partner() (which keys off env.user) resolves it.
        cls.user = new_test_user(env, login='zz_addr_user', groups='base.group_portal')
        cls.partner = cls.user.partner_id
        # The main partner needs a country for _get_fiscal_position to proceed;
        # the delivery address country is what actually selects the position.
        cls.partner.country_id = cls.country_a
        cls.shipping = env['res.partner'].create({
            'name': 'ZZ Ship', 'type': 'delivery', 'parent_id': cls.partner.id,
            'country_id': cls.country_a.id,
        })
        cls.order = env['sale.order'].create({
            'partner_id': cls.partner.id,
            'partner_shipping_id': cls.shipping.id,
            'website_id': cls.website.id,
        })
        cls.order._compute_fiscal_position_id()
        env.flush_all()

    def test_fiscal_position_follows_new_shipping_country(self):
        # Precondition: shipping in country A -> fiscal position A.
        self.assertEqual(self.order.fiscal_position_id, self.fp_a)

        env = self.env(user=self.user)
        info = _Info(env)
        with MockRequest(env, website=self.website, sale_order_id=self.order.id):
            UpdateAddress.mutate(
                None, info,
                {'id': self.shipping.id, 'country_id': self.country_b.id})

        # Address updated AND the fiscal position now reflects country B, proving
        # the recompute ran against the persisted (new) address, not the old one.
        self.assertEqual(self.shipping.country_id, self.country_b)
        self.assertEqual(self.order.fiscal_position_id, self.fp_b)
