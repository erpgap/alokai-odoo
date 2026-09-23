# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphene.types import generic

from odoo.exceptions import AccessError, UserError
from odoo.addons.graphql_alokai.graphql.registry import (
    mutation_registry,
    query_registry,
    type_registry,
)
CMS_EDITOR_GROUP = 'graphql_alokai.group_cms_editor'


def _check_editor(env):
    """Authorisation for every CMS write and every draft read.

    Deliberately not .sudo(). Every other mutation in this module sudoes, which
    is fine for a contact form and catastrophic for "write arbitrary content to
    any page on the site". These resolvers break the module's prevailing
    pattern on purpose - they look wrong next to their neighbours and are not.

    The storefront's cmsCanEdit flag is a UI affordance and is never trusted
    here.
    """
    if not env.user or not env.user.has_group(CMS_EDITOR_GROUP):
        raise AccessError('You do not have permission to edit content pages.')


def _page_for_editor(env, page_id):
    _check_editor(env)
    # No sudo: the record rules scope this to the websites of the user's
    # allowed companies, so one storefront's editor cannot reach another's.
    page = env['alokai.website.page'].browse(int(page_id))
    if not page.exists():
        raise UserError('That page no longer exists.')
    return page


# --------------------------------------------------------------------------- #
#                                   Types                                      #
# --------------------------------------------------------------------------- #

class PageRevision(graphene.ObjectType):
    id = graphene.Int()
    number = graphene.Int()
    blocks = generic.GenericScalar()
    created_at = graphene.String()
    author = graphene.String()
    restored_from = graphene.Int()
    is_live = graphene.Boolean()

    def resolve_created_at(self, info):
        return self.create_date.isoformat() if self.create_date else None

    def resolve_author(self, info):
        return self.create_uid.name if self.create_uid else None

    def resolve_restored_from(self, info):
        return self.restored_from_number or None

    def resolve_is_live(self, info):
        return self.page_id.live_revision_id.id == self.id


class CmsPage(graphene.ObjectType):
    id = graphene.Int()
    name = graphene.String()
    url = graphene.String()
    is_published = graphene.Boolean()
    meta_title = graphene.String()
    meta_description = graphene.String()
    blocks = generic.GenericScalar()
    draft_blocks = generic.GenericScalar()
    revision_count = graphene.Int()
    live_revision = graphene.Int()
    updated_at = graphene.String()
    has_unpublished_changes = graphene.Boolean()

    def resolve_meta_title(self, info):
        return self.website_meta_title or None

    def resolve_meta_description(self, info):
        return self.website_meta_description or None

    def resolve_blocks(self, info):
        # Published content only. The draft lives in a separate column, which
        # is what makes it impossible for an unpublished edit to leak.
        return (self.live_revision_id.blocks or []) if self.live_revision_id else []

    def resolve_draft_blocks(self, info):
        _check_editor(info.context['env'])
        return self.draft_blocks or []

    def resolve_live_revision(self, info):
        return self.live_revision_id.number if self.live_revision_id else None

    def resolve_updated_at(self, info):
        return self.write_date.isoformat() if self.write_date else None

    def resolve_has_unpublished_changes(self, info):
        live = (self.live_revision_id.blocks or []) if self.live_revision_id else []
        return (self.draft_blocks or []) != live


class CmsPageList(graphene.ObjectType):
    pages = graphene.List(CmsPage)
    total_count = graphene.Int()


# --------------------------------------------------------------------------- #
#                                  Queries                                     #
# --------------------------------------------------------------------------- #

class CmsQuery(graphene.ObjectType):
    cms_page = graphene.Field(
        CmsPage,
        slug=graphene.String(),
        id=graphene.Int(),
        description='Published page by URL. Public and cacheable.',
    )
    cms_pages = graphene.Field(
        CmsPageList,
        description='Page list for the studio. Requires the CMS Editor group.',
    )
    cms_page_draft = graphene.Field(
        CmsPage,
        id=graphene.Int(required=True),
        description='A page including its draft. Requires the CMS Editor group.',
    )
    cms_revisions = graphene.List(
        PageRevision,
        page_id=graphene.Int(required=True),
        description='Revision history. Requires the CMS Editor group.',
    )

    @staticmethod
    def resolve_cms_page(self, info, slug=None, id=None):
        env = info.context['env']
        # Public read, so sudo is correct here - it mirrors the existing
        # website_page resolver. is_published is the gate.
        Page = env['alokai.website.page'].sudo()

        domain = env['website'].get_current_website().website_domain()
        domain += [('is_published', '=', True), ('live_revision_id', '!=', False)]

        if id:
            domain += [('id', '=', id)]
        elif slug:
            domain += [('url', '=', slug)]
        else:
            return None

        return Page.search(domain, limit=1) or None

    @staticmethod
    def resolve_cms_pages(self, info):
        env = info.context['env']
        _check_editor(env)

        website = env['website'].get_current_website()
        pages = env['alokai.website.page'].search(
            [('website_id', 'in', (False, website.id))], order='write_date desc',
        )
        return CmsPageList(pages=pages, total_count=len(pages))

    @staticmethod
    def resolve_cms_page_draft(self, info, id):
        return _page_for_editor(info.context['env'], id)

    @staticmethod
    def resolve_cms_revisions(self, info, page_id):
        page = _page_for_editor(info.context['env'], page_id)
        return page.revision_ids.sorted(key=lambda r: r.number, reverse=True)


# --------------------------------------------------------------------------- #
#                                 Mutations                                    #
# --------------------------------------------------------------------------- #

class BlockReferencesInput(graphene.InputObjectType):
    """Ids the storefront found inside the blocks.

    Odoo cannot extract these itself - the blocks column is opaque to it - so
    the layer that understands the content mirrors them in. They exist to
    answer "which live pages reference product X?", which is how a product
    change invalidates the right pages.
    """
    product_tmpl_ids = graphene.List(graphene.Int)
    category_ids = graphene.List(graphene.Int)
    attachment_ids = graphene.List(graphene.Int)


def _references(payload):
    if not payload:
        return {}
    return {
        'product_tmpl_ids': payload.get('product_tmpl_ids') or [],
        'category_ids': payload.get('category_ids') or [],
        'attachment_ids': payload.get('attachment_ids') or [],
    }


class SaveCmsDraft(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)
        blocks = generic.GenericScalar(required=True)
        expected_write_date = graphene.String()

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id, blocks, expected_write_date=None):
        page = _page_for_editor(info.context['env'], page_id)
        return page.save_draft(blocks, expected_write_date=expected_write_date)


class PublishCmsPage(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)
        references = BlockReferencesInput()

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id, references=None):
        page = _page_for_editor(info.context['env'], page_id)
        page.publish_draft(references=_references(references))
        return page


class RestoreCmsRevision(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)
        revision_id = graphene.Int(required=True)

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id, revision_id):
        env = info.context['env']
        page = _page_for_editor(env, page_id)
        revision = env['alokai.page.revision'].browse(int(revision_id))

        if not revision.exists():
            raise UserError('That version is no longer available.')

        page.restore_revision(revision)
        return page


class DiscardCmsDraft(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id):
        return _page_for_editor(info.context['env'], page_id).discard_draft()


class UnpublishCmsPage(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id):
        return _page_for_editor(info.context['env'], page_id).unpublish_page()


class CreateCmsPage(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        url = graphene.String(required=True)
        blocks = generic.GenericScalar()

    Output = CmsPage

    @staticmethod
    def mutate(self, info, name, url, blocks=None):
        env = info.context['env']
        _check_editor(env)

        website = env['website'].get_current_website()
        url = (url or '').strip()

        if not url.startswith('/') or len(url) < 2:
            raise UserError('The page address must start with / and not be empty.')

        clash = env['alokai.website.page'].search([
            ('url', '=', url),
            ('website_id', 'in', (False, website.id)),
        ], limit=1)
        if clash:
            raise UserError('That address is already used by "%s".' % clash.name)

        return env['alokai.website.page'].create({
            'name': name,
            'url': url,
            'website_id': website.id,
            'draft_blocks': blocks or [],
        })


class UpdateCmsPage(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)
        name = graphene.String()
        url = graphene.String()
        meta_title = graphene.String()
        meta_description = graphene.String()

    Output = CmsPage

    @staticmethod
    def mutate(self, info, page_id, name=None, url=None,
               meta_title=None, meta_description=None):
        env = info.context['env']
        page = _page_for_editor(env, page_id)

        values = {}
        if name is not None:
            values['name'] = name
        if meta_title is not None:
            values['website_meta_title'] = meta_title
        if meta_description is not None:
            values['website_meta_description'] = meta_description

        if url is not None:
            url = url.strip()
            if not url.startswith('/') or len(url) < 2:
                raise UserError('The page address must start with / and not be empty.')

            clash = env['alokai.website.page'].search([
                ('url', '=', url),
                ('id', '!=', page.id),
                ('website_id', 'in', (False, page.website_id.id)),
            ], limit=1)
            if clash:
                raise UserError('That address is already used by "%s".' % clash.name)
            values['url'] = url

        page.write(values)
        return page


class DeleteCmsPage(graphene.Mutation):
    class Arguments:
        page_id = graphene.Int(required=True)

    Output = graphene.Boolean

    @staticmethod
    def mutate(self, info, page_id):
        page = _page_for_editor(info.context['env'], page_id)
        # live_revision_id is ondelete='restrict', so clear it before the
        # revisions cascade away with the page.
        page.live_revision_id = False
        page.unlink()
        return True


class CmsMutation(graphene.ObjectType):
    save_cms_draft = SaveCmsDraft.Field(description='Replace a page draft.')
    publish_cms_page = PublishCmsPage.Field(description='Copy the draft into a new live revision.')
    restore_cms_revision = RestoreCmsRevision.Field(description='Copy an older revision forward and make it live.')
    discard_cms_draft = DiscardCmsDraft.Field(description='Reset the draft to what is live.')
    unpublish_cms_page = UnpublishCmsPage.Field(description='Hide a page from visitors.')
    create_cms_page = CreateCmsPage.Field()
    update_cms_page = UpdateCmsPage.Field()
    delete_cms_page = DeleteCmsPage.Field()


query_registry.append(CmsQuery)
mutation_registry.append(CmsMutation)
type_registry.extend([PageRevision, CmsPage, CmsPageList])
