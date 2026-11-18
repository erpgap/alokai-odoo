# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
from types import SimpleNamespace

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.graphql_alokai.schemas.website import WebsiteQuery


@tagged('post_install', '-at_install')
class TestWebsiteMenu(TransactionCase):
    """The storefront header's top entries are the header menus with no
    parent. Odoo's own top menu of the website stays, hidden and empty."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Menu = cls.env['website.menu']
        # Odoo gives a new website its top menu and the default entries.
        cls.website = cls.env['website'].create({'name': 'Menu test'})
        cls.main = cls.Menu._alokai_main_menus([cls.website.id])

    def _top_entries(self):
        return self.Menu.search([
            ('website_id', '=', self.website.id), ('parent_id', '=', False),
            ('is_footer', '=', False), ('is_main_menu', '=', False),
        ])

    def _header(self):
        env = self.env(context=dict(self.env.context, website_id=self.website.id))
        return WebsiteQuery.resolve_website_header_menu(None, SimpleNamespace(context={'env': env}))

    def test_a_new_website_has_its_entries_at_the_top_level(self):
        self.assertEqual(len(self.main), 1)
        self.assertFalse(self.main.child_id, 'the hidden top menu stays empty')
        self.assertTrue(self._top_entries(), "Odoo's default entries moved up")
        self.assertEqual(self.website.menu_id, self.main)

    def test_an_entry_without_parent_is_a_top_entry(self):
        menu = self.Menu.create({'name': 'Sale', 'url': '/sale', 'website_id': self.website.id})
        self.assertFalse(menu.parent_id)
        self.assertIn(menu, self._header())
        self.assertNotIn(self.main, self._header())

    def test_the_hidden_top_menu_is_never_a_parent(self):
        menu = self.Menu.create({'name': 'Sale', 'url': '/sale', 'website_id': self.website.id, 'parent_id': self.main.id})
        self.assertFalse(menu.parent_id)
        other = self.Menu.create({'name': 'Women', 'url': '#', 'website_id': self.website.id})
        child = self.Menu.create({'name': 'Dresses', 'url': '/dresses', 'website_id': self.website.id, 'parent_id': other.id})
        self.assertEqual(child.parent_id, other, 'a real parent is kept')
        child.write({'parent_id': self.main.id})
        self.assertFalse(child.parent_id)

    def test_an_entry_first_by_sequence_does_not_replace_the_hidden_top_menu(self):
        self.Menu.create({'name': 'First', 'url': '/first', 'website_id': self.website.id, 'sequence': -10})
        self.website.invalidate_recordset(['menu_id'])
        self.assertEqual(self.website.menu_id, self.main)
        self.assertEqual(self.Menu._alokai_main_menus([self.website.id]), self.main)

    def test_a_module_menu_lands_at_the_top_level(self):
        # As a module adds one: no website, under Odoo's default main menu.
        self.Menu.create({'name': 'Events', 'url': '/event', 'parent_id': self.env.ref('website.main_menu').id})
        events = self._top_entries().filtered(lambda m: m.name == 'Events')
        self.assertEqual(len(events), 1)
        self.assertFalse(self.main.child_id)

    def test_footer_columns_have_no_parent_and_are_not_header_entries(self):
        column = self.Menu.create({'name': 'Help', 'url': '#', 'website_id': self.website.id, 'is_footer': True})
        self.assertFalse(column.parent_id)
        self.assertFalse(column.is_main_menu)
        self.assertNotIn(column, self._header())

    def test_is_main_menu_can_be_searched(self):
        self.assertIn(self.main, self.Menu.search([('is_main_menu', '=', True)]))
        self.assertNotIn(self.main, self.Menu.search([('is_main_menu', '=', False)]))
        self.assertTrue(self.main.is_main_menu)

    def test_a_new_entry_from_the_menu_screens_has_the_website(self):
        # Header Menu and Footer Menu: the website being edited, so Parent
        # Menu has something to offer.
        website = self.env['website'].get_current_website()
        defaults = self.Menu.with_context(alokai_menu_form=1, default_is_footer=True).default_get(['website_id', 'is_footer'])
        self.assertEqual(defaults.get('website_id'), website.id)
        # Elsewhere no website stays the default: a menu for every website.
        self.assertFalse(self.Menu.default_get(['website_id']).get('website_id'))
