# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWebsiteMenu(TransactionCase):
    """A header entry saved without a parent belongs under its website's top
    menu, the one the storefront header lists; footer entries keep theirs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env['website.menu']
        # Odoo gives a new website its top menu, copied from the default one.
        cls.website = cls.env['website'].create({'name': 'Menu test'})
        cls.top = cls.Menu.search([('website_id', '=', cls.website.id), ('parent_id', '=', False)])

    def test_a_new_website_keeps_its_top_menu(self):
        # Created when the website had no menu yet: no parent to go under.
        self.assertEqual(len(self.top), 1)
        self.assertTrue(all(child.parent_id == self.top for child in self.top.child_id))

    def test_header_entry_without_parent_goes_under_the_top_menu(self):
        menu = self.Menu.create({'name': 'Sale', 'url': '/sale', 'website_id': self.website.id, 'parent_id': False})
        self.assertEqual(menu.parent_id, self.top)

    def test_a_given_parent_is_kept(self):
        parent = self.Menu.create({'name': 'Women', 'url': '#', 'website_id': self.website.id})
        child = self.Menu.create({'name': 'Dresses', 'url': '/dresses', 'website_id': self.website.id, 'parent_id': parent.id})
        self.assertEqual(child.parent_id, parent)

    def test_clearing_the_parent_puts_it_back_under_the_top_menu(self):
        parent = self.Menu.create({'name': 'Women', 'url': '#', 'website_id': self.website.id})
        child = self.Menu.create({'name': 'Dresses', 'url': '/dresses', 'website_id': self.website.id, 'parent_id': parent.id})
        child.write({'parent_id': False})
        self.assertEqual(child.parent_id, self.top)
        # The top menu itself stays without a parent.
        self.top.write({'parent_id': False})
        self.assertFalse(self.top.parent_id)

    def test_footer_columns_keep_no_parent(self):
        column = self.Menu.create({'name': 'Help', 'url': '#', 'website_id': self.website.id, 'is_footer': True})
        self.assertFalse(column.parent_id)
        from_action = self.Menu.with_context(default_is_footer=True).create({'name': 'About', 'url': '#', 'website_id': self.website.id})
        self.assertFalse(from_action.parent_id)
