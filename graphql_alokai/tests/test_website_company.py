# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import json

from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged('post_install', '-at_install')
class TestAlokaiWebsiteCompany(HttpCase):
    """A request runs in the company of the website it targets, not in the
    user's default company: taxes are filtered by `env.company`, so a user
    whose default company is another one would otherwise get that company's
    taxes applied to the price.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company_a = env.company
        company_b = env['res.company'].create({
            'name': 'Alokai Company B',
            'currency_id': company_a.currency_id.id,
            'country_id': company_a.country_id.id,
        })

        cls.website = env['website'].create({
            'name': 'Alokai Company A Website',
            'company_id': company_a.id,
            'alokai_domain': 'shop-a.alokai-company-test.example',
        })

        Tax = env['account.tax']
        tax_a = Tax.create({
            'name': 'Alokai company test 0%',
            'amount': 0.0,
            'type_tax_use': 'sale',
            'company_id': company_a.id,
        })
        tax_b = Tax.create({
            'name': 'Alokai company test 21% included',
            'amount': 21.0,
            'price_include_override': 'tax_included',
            'type_tax_use': 'sale',
            'company_id': company_b.id,
            # A fresh company has no chart of accounts, hence no tax group.
            'tax_group_id': env['account.tax.group'].create({
                'name': 'Alokai company test group',
                'company_id': company_b.id,
            }).id,
        })
        cls.product = env['product.template'].create({
            'name': 'Alokai Company Test Product',
            'list_price': 121.0,
            'sale_ok': True,
            'is_published': True,
            'website_id': cls.website.id,
            'taxes_id': [(6, 0, (tax_a | tax_b).ids)],
        })

        for login, company in (('alokai_company_a_portal', company_a),
                               ('alokai_company_b_portal', company_b)):
            new_test_user(
                env,
                login=login,
                password=login,
                groups='base.group_portal',
                company_id=company.id,
                company_ids=[(6, 0, (company_a | company_b).ids)],
            )

    def _combination_info_as(self, login):
        self.authenticate(login, login)
        query = 'query { product(id: %d) { combinationInfo } }' % self.product.id
        response = self.url_open(
            '/graphql/alokai',
            data=json.dumps({'query': query}),
            headers={
                'Content-Type': 'application/json',
                'Request-Host': 'https://shop-a.alokai-company-test.example',
            },
        )
        body = response.json()
        self.assertNotIn('errors', body, body)
        return body['data']['product']['combinationInfo']

    def test_price_ignores_user_default_company(self):
        same_company = self._combination_info_as('alokai_company_a_portal')
        other_company = self._combination_info_as('alokai_company_b_portal')
        for key in ('price', 'list_price'):
            self.assertAlmostEqual(other_company[key], same_company[key], places=2, msg=key)
