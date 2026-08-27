# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

{
    'name': 'Alokai',
    'version': '19.0.1.0.0',
    'summary': 'GraphQL API connecting Odoo to the Alokai (Vue Storefront) headless frontend',
    'description': """
Alokai
======

Headless eCommerce backend that exposes Odoo through a GraphQL API for the
Alokai (Vue Storefront) frontend.

Features
--------
* GraphQL endpoint (``/graphql/alokai``) with a GraphiQL IDE for development.
* Storefront queries & mutations: products, categories, blog, cart, checkout,
  wishlist, addresses, orders and user account.
* Redis-backed stock cache kept in sync by lightweight crons (incremental
  "dirty" updates plus a daily full resync).
* Redis slug sync providing a dynamic-route fallback for records created after
  a storefront build.
* On-demand frontend cache invalidation when records change.
* Merchandising: frequently-bought-together and product popularity.
""",
    'category': 'Website/eCommerce',
    'application': True,
    'license': 'LGPL-3',
    'author': 'ERPGAP',
    'website': 'https://www.erpgap.com/alokai/',
    'depends': [
        'graphql_base',
        'website',
        'website_sale_wishlist',
        'website_mass_mailing',
        'website_sale_loyalty',
        'stock',
        'auth_signup',
        'contacts',
        'crm',
        'theme_default',
        'auth_totp',
        'website_blog'
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/mail_template.xml',
        'data/website_data.xml',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron_data.xml',
        'views/product_views.xml',
        'views/website_views.xml',
        'views/alokai_website_page_views.xml',
        'views/website_blog_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu.xml'
    ],
    'demo': [
        'data/demo_ir_config_parameter.xml',
        'data/demo_company.xml',
        'data/demo_product_attribute.xml',
        'data/demo_product_public_category.xml',
        'data/demo_products_men_accessories_bags.xml',
        'data/demo_products_men_accessories_wallets.xml',
        'data/demo_products_men_bottoms_jeans.xml',
        'data/demo_products_men_bottoms_trousers.xml',
        'data/demo_products_men_outerwear_blazers.xml',
        'data/demo_products_men_outerwear_jackets.xml',
        'data/demo_products_men_shirts.xml',
        'data/demo_products_men_shoes_dress.xml',
        'data/demo_products_men_shoes_sneakers.xml',
        'data/demo_products_men_suits.xml',
        'data/demo_products_men_t-shirts.xml',
        'data/demo_products_women_accessories_bags_clutch.xml',
        'data/demo_products_women_accessories_bags_handbag.xml',
        'data/demo_products_women_accessories_bags_shopper.xml',
        'data/demo_products_women_accessories_bags_shoulder.xml',
        'data/demo_products_women_accessories_wallets.xml',
        'data/demo_products_women_bottoms_jeans.xml',
        'data/demo_products_women_bottoms_skirts.xml',
        'data/demo_products_women_bottoms_trousers.xml',
        'data/demo_products_women_dresses.xml',
        'data/demo_products_women_outerwear_blazers.xml',
        'data/demo_products_women_outerwear_jackets.xml',
        'data/demo_products_women_shirts.xml',
        'data/demo_products_women_shoes_boots.xml',
        'data/demo_products_women_shoes_flats.xml',
        'data/demo_products_women_shoes_heels.xml',
        'data/demo_products_women_shoes_sandals.xml',
        'data/demo_products_women_shoes_sneakers.xml',
        'data/demo_products_women_t-shirts.xml',
        'data/demo_products_women_tops.xml',
        'data/remove_default_category_product.xml',
    ],
    'installable': True,
    'auto_install': False,
    'pre_init_hook': 'pre_init_hook_login_check',
    'post_init_hook': 'post_init_hook_login_convert',
}