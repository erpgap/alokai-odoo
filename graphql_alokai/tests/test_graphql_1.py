# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json
from odoo.tests import tagged
from odoo.tests.common import HttpCase


@tagged('post_install', '-at_install', 'alokai')
class TestGraphqlAlokai(HttpCase):

    def test_graphql_linting_categories(self):
        # Define your GraphQL query; you can modify fields as needed
        query = """
        {
          categories (filter: {id:1}){
            categories {
              id
              name
            }
          }
        }
        """
        response = self.url_open(
            '/graphql/alokai',
            data=json.dumps({'query': query}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200, "Should return HTTP 200 OK")

        body = json.loads(response.text)
        self.assertIn('data', body)
        self.assertTrue(
            all('name' in product_dict for product_dict in body['data']['categories']['categories']),
            "All product dictionaries must have a 'name' key"
        )
        self.assertTrue(
            all('id' in product_dict for product_dict in body['data']['categories']['categories']),
            "All product dictionaries must have a 'id' key"
        )

    def test_graphql_linting_products(self):
        # Define your GraphQL query; you can modify fields as needed
        query = """
        {
          products {
            products {
              id
              name
            }
          }
        }
        """
        response = self.url_open(
            '/graphql/alokai',
            data=json.dumps({'query': query}),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status_code, 200, "Should return HTTP 200 OK")

        body = json.loads(response.text)
        self.assertIn('data', body)
        self.assertTrue(
            all('name' in product_dict for product_dict in body['data']['products']['products']),
            "All product dictionaries must have a 'name' key"
        )
        self.assertTrue(
            all('id' in product_dict for product_dict in body['data']['products']['products']),
            "All product dictionaries must have a 'id' key"
        )
