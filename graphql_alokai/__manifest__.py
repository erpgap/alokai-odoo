# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

{
    'name': 'Alokai Api',
    'version': '18.0.1.0.0',
    'summary': 'Alokai API',
    'description': """Alokai API Integration""",
    'category': 'Website',
    'license': 'LGPL-3',
    'author': 'ERPGAP',
    'website': 'https://www.erpgap.com/',
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
        'payment_adyen_alokai',
        'payment_stripe_alokai',
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
        'views/website_blog_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu.xml'
    ],
    'demo': [
        'data/demo_product_attribute.xml',
        'data/demo_product_public_category.xml',
        'data/demo_products_women_clothing.xml',
        'data/demo_products_women_shoes.xml',
        'data/demo_products_women_bags.xml',
        'data/demo_products_men_clothing_1.xml',
        'data/demo_products_men_clothing_2.xml',
        'data/demo_products_men_clothing_3.xml',
        'data/demo_products_men_clothing_4.xml',
        'data/demo_products_men_shoes.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'graphql_alokai/static/src/client_actions/website_preview/website_preview.js',
        ]
    },
    'installable': True,
    'auto_install': False,
    'pre_init_hook': 'pre_init_hook_login_check',
    'post_init_hook': 'post_init_hook_login_convert',
}