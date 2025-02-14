# -*- coding: utf-8 -*-
# Copyright 2025 ODOOGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

{
    "name": "Web widget Markdown",
    "summary": """
        Add support of markdown content into an Odoo widget form.
    """,
    "description": """
        Allow the use of the widget markdown to display markdown content into Odoo views 
        in render mode and a markdown Editor in edit mode thanks to easyMDE Javascript library
    """,
    'author': 'ERPGap',
    'website': 'https://www.erpgap.com/',
    "category": "web",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "depends": ["base", "web"],
    "assets": {
        "web.assets_backend": [
            'web_widget_markdown/static/src/views/fields/**/*',
        ],
        "web_widget_markdown.easymdejs_lib" : [
            "/web_widget_markdown/static/lib/easymde.min.js",
            "/web_widget_markdown/static/lib/marked.min.js",
            "/web_widget_markdown/static/lib/easymde.min.css",
        ]
    },
    "auto_install": False,
    "installable": True,
}
