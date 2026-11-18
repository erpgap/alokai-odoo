# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import graphene
from graphene.types import generic
from odoo.addons.graphql_alokai.schemas.objects import Website, WebsiteMenu, get_image_filename
from odoo.addons.graphql_alokai.graphql.registry import query_registry, type_registry


class Homepage(graphene.Interface):
    meta_title = graphene.String()
    meta_keyword = graphene.String()
    meta_description = graphene.String()
    meta_image = graphene.String()
    meta_image_filename = graphene.String()
    json_ld = generic.GenericScalar()


class HomepageList(graphene.ObjectType):
    class Meta:
        interfaces = (Homepage,)


class WebsiteQuery(graphene.ObjectType):
    website_menu = graphene.List(
        graphene.NonNull(WebsiteMenu),
        no_parent=graphene.Boolean(),
    )
    website_header_menu = graphene.List(
        graphene.NonNull(WebsiteMenu),
        description='The storefront header: the website\'s top entries (header '
                    'menus without a parent), each with its submenu in childs.',
    )
    website_mega_menu = graphene.List(
        graphene.NonNull(WebsiteMenu),
        no_parent=graphene.Boolean(),
    )
    website_footer = graphene.List(
        graphene.NonNull(WebsiteMenu),
        no_parent=graphene.Boolean(),
    )
    website_homepage = graphene.Field(
        Homepage,
    )
    website = graphene.Field(
        Website,
        description='The website this request belongs to, resolved from its '
                    'Request-Host. Lets the storefront show the store\'s own '
                    'name rather than one fixed at build time.',
    )

    @staticmethod
    def resolve_website(self, info):
        return info.context['env']['website'].get_current_website()

    @staticmethod
    def resolve_website_menu(self, info, no_parent=False):
        env = info.context['env']
        website = env['website'].get_current_website()

        domain = [
            ('website_id', '=', website.id),
            ('is_footer', '=', False),
            ('is_mega_menu', '=', False),
        ]

        if no_parent:
            domain += [('parent_id', '=', False)]

        return env['website.menu'].search(domain).filtered(lambda m: m.is_visible)

    @staticmethod
    def resolve_website_header_menu(self, info):
        env = info.context['env']
        website = env['website'].get_current_website()
        Menu = env['website.menu']
        # Not the hidden top menu Odoo keeps; anything still under it counts
        # as a top entry too (moved up on the next update).
        main_menus = Menu._alokai_main_menus([website.id])
        menus = Menu.search([
            ('website_id', '=', website.id),
            ('is_footer', '=', False),
            ('is_mega_menu', '=', False),
            '|', ('parent_id', '=', False), ('parent_id', 'in', main_menus.ids),
        ])
        return (menus - main_menus).filtered(lambda m: m.is_visible)

    @staticmethod
    def resolve_website_mega_menu(self, info, no_parent=False):
        env = info.context['env']
        website = env['website'].get_current_website()

        domain = [
            ('website_id', '=', website.id),
            ('is_footer', '=', False),
            ('is_mega_menu', '=', True),
        ]

        if no_parent:
            domain += [('parent_id', '=', False)]

        return env['website.menu'].search(domain).filtered(lambda m: m.is_visible)

    @staticmethod
    def resolve_website_footer(self, info, no_parent=False):
        env = info.context['env']
        website = env['website'].get_current_website()

        domain = [
            ('website_id', '=', website.id),
            ('is_footer', '=', True),
            ('is_mega_menu', '=', False),
        ]

        if no_parent:
            domain += [('parent_id', '=', False)]

        return env['website.menu'].search(domain).filtered(lambda m: m.is_visible)

    @staticmethod
    def resolve_website_homepage(self, info):
        env = info.context['env']
        website = env['website'].get_current_website()

        # `or None` on each: an unset Char is False in Odoo, and graphene
        # coerces that into the string "false" rather than a null, which the
        # storefront then renders as a description reading "false".
        return HomepageList(
            meta_title=website.website_meta_title or None,
            meta_keyword=website.website_meta_keywords or None,
            meta_description=website.website_meta_description or None,
            meta_image=f'/web/image/website/{website.id}/website_meta_img',
            meta_image_filename=get_image_filename(website, name='website_meta_title'),
            json_ld=website.json_ld,
        )

query_registry.append(WebsiteQuery)
type_registry.append(HomepageList)
