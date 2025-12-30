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
        'website_sale',
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
        'views/menu.xml',
    ],
    'demo': [
        'data/remove_default_category_product.xml',
        'data/demo_product_attribute.xml',
        'data/demo_product_public_category.xml',
        'data/demo_products_bedroom_furniture.xml',
        'data/demo_products_home_office.xml',
        'data/demo_products_lighting.xml',
        'data/demo_products_living_room_seating.xml',
        'data/demo_products_storage_shelving.xml',
        'data/demo_products_tables.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'graphql_alokai/static/src/client_actions/website_preview/website_preview.js',
        ]
    },
    "installable": True,
    "application": False,
    'auto_install': False,
    'pre_init_hook': 'pre_init_hook_login_check',
    'post_init_hook': 'post_init_hook_login_convert',
}
