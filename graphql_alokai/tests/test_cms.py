# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Tests for the CMS: page content, revisions, security and GraphQL.

Several of these exist because the behaviour they pin down was got wrong the
first time and the mistake was invisible to inspection:

  * ``test_restore_survives_pruning_its_own_source`` - creating a revision
    prunes, and with a small limit the revision being restored FROM is itself
    a deletion candidate, so the code read a record that no longer existed.
  * ``test_product_write_invalidates_featuring_pages`` - the hook was added as
    a new ``write`` on a class that already defined one further down its body,
    which silently replaced it. Invalidation queued nothing and the code looked
    correct.
  * ``test_empty_blocks_are_valid`` - Odoo's Json field reads back ``False``
    rather than ``None``, so a guard checking identity rejected empty pages.
"""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from .common import AlokaiGraphQLCommon


def block(title='Hello', block_type='hero'):
    """A minimal well-formed block. Text is per-language, as stored."""
    return {
        'id': 'b1',
        'blockType': block_type,
        'schemaVersion': 1,
        'data': {'title': {'en_US': title}},
    }


@tagged('post_install', '-at_install')
class TestCmsModel(TransactionCase):
    """Content storage, revisions and the structural backstop."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env['alokai.website.page']
        cls.Revision = cls.env['alokai.page.revision']
        cls.page = cls.Page.create({'name': 'Test Page', 'url': '/test-page'})

    def _set_limit(self, value):
        self.env['ir.config_parameter'].sudo().set_param(
            'alokai_cms_revision_limit', str(value))

    # --- structural validation -------------------------------------------

    def test_empty_blocks_are_valid(self):
        """A page with no blocks is empty, not broken.

        Odoo's Json field reads back False for an empty value rather than
        None, so a guard checking `is None` rejected every empty page.
        """
        self.page.draft_blocks = []
        self.assertFalse(self.page.draft_blocks)

        page = self.Page.create({'name': 'Empty', 'url': '/empty'})
        self.assertFalse(page.draft_blocks)

    def test_blocks_must_be_a_list(self):
        with self.assertRaises(ValidationError):
            self.page.draft_blocks = {'not': 'a list'}

    def test_block_must_be_an_object_with_a_type(self):
        with self.assertRaises(ValidationError):
            self.page.draft_blocks = ['just a string']

        with self.assertRaises(ValidationError):
            self.page.draft_blocks = [{'data': {}}]

        with self.assertRaises(ValidationError):
            self.page.draft_blocks = [{'blockType': '   ', 'data': {}}]

    def test_block_data_must_be_an_object(self):
        with self.assertRaises(ValidationError):
            self.page.draft_blocks = [{'blockType': 'hero', 'data': ['nope']}]

    def test_unknown_block_type_is_accepted_by_odoo(self):
        """Odoo does not know what a block is, and must not pretend to.

        Block types are the storefront's vocabulary. If Odoo rejected unknown
        ones, adding a block would require an Odoo release - which is the
        coupling this design exists to avoid.
        """
        self.page.draft_blocks = [{'blockType': 'somethingNew', 'data': {}}]
        self.assertEqual(self.page.draft_blocks[0]['blockType'], 'somethingNew')

    def test_too_many_blocks_rejected(self):
        with self.assertRaises(ValidationError):
            self.page.draft_blocks = [block()] * 201

    # --- publishing -------------------------------------------------------

    def test_publish_creates_a_revision_and_points_the_page_at_it(self):
        self.page.draft_blocks = [block('v1')]
        revision = self.page.publish_draft()

        self.assertEqual(revision.number, 1)
        self.assertEqual(self.page.live_revision_id, revision)
        self.assertTrue(self.page.is_published)
        self.assertEqual(revision.blocks[0]['data']['title']['en_US'], 'v1')

    def test_publish_copies_rather_than_references(self):
        """Editing the draft afterwards must not change what is live."""
        self.page.draft_blocks = [block('published')]
        self.page.publish_draft()

        self.page.draft_blocks = [block('edited after publish')]

        self.assertEqual(
            self.page.live_revision_id.blocks[0]['data']['title']['en_US'],
            'published',
        )

    def test_revision_numbers_increment_per_page(self):
        other = self.Page.create({'name': 'Other', 'url': '/other'})

        self.page.draft_blocks = [block()]
        other.draft_blocks = [block()]

        self.assertEqual(self.page.publish_draft().number, 1)
        self.assertEqual(self.page.publish_draft().number, 2)
        # Numbering is per page, not global.
        self.assertEqual(other.publish_draft().number, 1)

    def test_revisions_are_immutable(self):
        self.page.draft_blocks = [block()]
        revision = self.page.publish_draft()

        with self.assertRaises(UserError):
            revision.blocks = [block('tampered')]

        with self.assertRaises(UserError):
            revision.number = 99

    def test_reference_columns_are_writable_after_create(self):
        """The one mutation a revision accepts, used to mirror references in."""
        self.page.draft_blocks = [block()]
        product = self.env['product.template'].create({'name': 'Ref Product'})

        revision = self.page.publish_draft(
            references={'product_tmpl_ids': product.ids})

        self.assertEqual(revision.product_tmpl_ids, product)

    def test_duplicate_revision_number_rejected(self):
        self.page.draft_blocks = [block()]
        self.page.publish_draft()

        with self.assertRaises(Exception):
            self.Revision.create({
                'page_id': self.page.id, 'number': 1, 'blocks': [],
            })
            self.env.cr.flush()

    # --- pruning ----------------------------------------------------------

    def test_pruning_keeps_the_newest_revisions(self):
        self._set_limit(3)
        self.page.draft_blocks = [block()]

        for _ in range(5):
            self.page.publish_draft()

        kept = self.Revision.search([('page_id', '=', self.page.id)])
        self.assertEqual(sorted(kept.mapped('number')), [3, 4, 5])

    def test_live_revision_is_always_the_newest(self):
        """Which is why pruning needs no exception for it."""
        self._set_limit(2)
        self.page.draft_blocks = [block()]

        for _ in range(4):
            self.page.publish_draft()

        kept = self.Revision.search([('page_id', '=', self.page.id)])
        self.assertEqual(
            self.page.live_revision_id.number, max(kept.mapped('number')))

    def test_revision_limit_falls_back_when_misconfigured(self):
        for bad in ('nonsense', '0', '-5', ''):
            self._set_limit(bad)
            self.assertEqual(self.Revision._revision_limit(), 10)

    # --- restore ----------------------------------------------------------

    def test_restore_copies_forward_rather_than_moving_a_pointer(self):
        self._set_limit(10)
        self.page.draft_blocks = [block('first')]
        first = self.page.publish_draft()

        self.page.draft_blocks = [block('second')]
        self.page.publish_draft()

        restored = self.page.restore_revision(first)

        # A NEW revision, not the old one made live again.
        self.assertEqual(restored.number, 3)
        self.assertEqual(self.page.live_revision_id, restored)
        self.assertEqual(
            restored.blocks[0]['data']['title']['en_US'], 'first')
        # The original is untouched and still in the history.
        self.assertTrue(first.exists())
        self.assertEqual(first.number, 1)

    def test_restore_resets_the_draft_to_match(self):
        self.page.draft_blocks = [block('first')]
        first = self.page.publish_draft()
        self.page.draft_blocks = [block('work in progress')]

        self.page.restore_revision(first)

        self.assertEqual(
            self.page.draft_blocks[0]['data']['title']['en_US'], 'first')

    def test_restore_survives_pruning_its_own_source(self):
        """Regression: restoring read a record its own prune had deleted.

        Content survives because restore copies forward. The record does not,
        which is why the audit trail is also a plain integer.
        """
        self._set_limit(3)
        self.page.draft_blocks = [block('oldest')]
        oldest = self.page.publish_draft()

        for i in range(2):
            self.page.draft_blocks = [block('later-%s' % i)]
            self.page.publish_draft()

        restored = self.page.restore_revision(oldest)

        self.assertEqual(
            restored.blocks[0]['data']['title']['en_US'], 'oldest')
        self.assertEqual(restored.restored_from_number, 1)
        # The source was pruned by this very call; the durable audit survives.
        self.assertFalse(oldest.exists())
        self.assertFalse(restored.restored_from_id)

    def test_restore_records_its_source(self):
        self._set_limit(10)
        self.page.draft_blocks = [block('a')]
        first = self.page.publish_draft()
        self.page.draft_blocks = [block('b')]
        self.page.publish_draft()

        restored = self.page.restore_revision(first)

        self.assertEqual(restored.restored_from_id, first)
        self.assertEqual(restored.restored_from_number, first.number)

    def test_restore_rejects_a_revision_from_another_page(self):
        other = self.Page.create({'name': 'Other', 'url': '/other-page'})
        other.draft_blocks = [block()]
        foreign = other.publish_draft()

        with self.assertRaises(UserError):
            self.page.restore_revision(foreign)

    # --- draft lifecycle --------------------------------------------------

    def test_discard_resets_the_draft_to_live(self):
        self.page.draft_blocks = [block('live')]
        self.page.publish_draft()
        self.page.draft_blocks = [block('unsaved')]

        self.page.discard_draft()

        self.assertEqual(
            self.page.draft_blocks[0]['data']['title']['en_US'], 'live')

    def test_discard_on_never_published_page_empties_the_draft(self):
        self.page.draft_blocks = [block('never published')]
        self.page.discard_draft()
        self.assertFalse(self.page.draft_blocks)

    def test_unpublish_hides_the_page_but_keeps_its_content(self):
        self.page.draft_blocks = [block()]
        revision = self.page.publish_draft()

        self.page.unpublish_page()

        self.assertFalse(self.page.is_published)
        self.assertEqual(self.page.live_revision_id, revision)

    # --- concurrency ------------------------------------------------------

    def test_stale_write_date_is_rejected(self):
        """Whole-document drafts would otherwise clobber another editor."""
        import datetime
        self.page.draft_blocks = [block('theirs')]

        with self.assertRaises(UserError):
            self.page.save_draft(
                [block('mine')],
                expected_write_date=datetime.datetime(2020, 1, 1),
            )

    def test_current_write_date_is_accepted(self):
        self.page.save_draft(
            [block('ok')], expected_write_date=self.page.write_date)
        self.assertEqual(
            self.page.draft_blocks[0]['data']['title']['en_US'], 'ok')

    def test_save_draft_without_a_write_date_skips_the_check(self):
        self.page.save_draft([block('no check')])
        self.assertEqual(
            self.page.draft_blocks[0]['data']['title']['en_US'], 'no check')


@tagged('post_install', '-at_install')
class TestCmsSystemPagesAndRegions(TransactionCase):
    """The homepage, and the slots inside pages the storefront owns."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env['alokai.website.page']

    # --- system pages -----------------------------------------------------

    def test_a_system_page_cannot_be_deleted(self):
        """Its content is the merchant's; its existence is not."""
        page = self.Page.create({
            'name': 'Home', 'url': '/sys-home', 'is_system': True})

        with self.assertRaises(UserError):
            page.unlink()

    def test_a_system_page_cannot_be_unpublished(self):
        """Unpublishing leaves the route as empty as deleting would."""
        page = self.Page.create({
            'name': 'Home', 'url': '/sys-live', 'is_system': True})
        page.publish_draft()

        with self.assertRaises(UserError):
            page.unpublish_page()
        self.assertTrue(page.is_published)

    def test_an_unpublished_system_page_can_be_published(self):
        page = self.Page.create({
            'name': 'Home', 'url': '/sys-draft', 'is_system': True,
            'is_published': False})

        page.publish_draft()
        self.assertTrue(page.is_published)

    def test_a_system_page_url_is_fixed(self):
        page = self.Page.create({
            'name': 'Home', 'url': '/sys-fixed', 'is_system': True})

        with self.assertRaises(UserError):
            page.url = '/somewhere-else'

    def test_a_system_page_content_is_fully_editable(self):
        page = self.Page.create({
            'name': 'Home', 'url': '/sys-editable', 'is_system': True})

        page.draft_blocks = [block('merchant copy')]
        page.publish_draft()

        self.assertEqual(
            page.live_revision_id.blocks[0]['data']['title']['en_US'],
            'merchant copy')

    def test_an_ordinary_page_is_still_deletable(self):
        page = self.Page.create({'name': 'Ordinary', 'url': '/ordinary'})
        page.unlink()
        self.assertFalse(page.exists())

    def test_seed_homepage_is_idempotent(self):
        """Re-running after a release must not overwrite merchant content."""
        first = self.Page.seed_homepage([block('default copy')])
        self.assertTrue(first.is_system)
        self.assertTrue(first.is_published)

        first.draft_blocks = [block('what the merchant wrote')]
        first.publish_draft()

        again = self.Page.seed_homepage([block('default copy')])

        self.assertEqual(again, first)
        self.assertEqual(
            again.live_revision_id.blocks[0]['data']['title']['en_US'],
            'what the merchant wrote')

    # --- regions ----------------------------------------------------------

    def test_a_region_needs_no_url(self):
        region = self.Page.seed_region('test-slot', 'A test slot')
        self.assertEqual(region.kind, 'region')
        self.assertFalse(region.url)

    def test_a_page_still_needs_a_url(self):
        with self.assertRaises(ValidationError):
            self.Page.create({'name': 'No address', 'kind': 'page'})

    def test_a_region_needs_a_key(self):
        with self.assertRaises(ValidationError):
            self.Page.create({'name': 'No key', 'kind': 'region'})

    def test_seed_region_is_idempotent(self):
        first = self.Page.seed_region('once', 'Once')
        first.draft_blocks = [block('merchant copy')]
        first.publish_draft()

        again = self.Page.seed_region('once', 'Once')

        self.assertEqual(again, first)
        self.assertEqual(
            again.live_revision_id.blocks[0]['data']['title']['en_US'],
            'merchant copy')

    def test_a_region_starts_empty(self):
        """So installing this changes nothing a visitor sees."""
        region = self.Page.seed_region('starts-empty', 'Starts empty')
        self.assertFalse(region.draft_blocks)
        self.assertFalse(region.live_revision_id)

    def test_a_region_cannot_be_deleted(self):
        region = self.Page.seed_region('permanent', 'Permanent')
        with self.assertRaises(UserError):
            region.unlink()

    def test_regions_use_the_same_revision_machinery(self):
        region = self.Page.seed_region('versioned', 'Versioned')

        region.draft_blocks = [block('first')]
        first = region.publish_draft()
        region.draft_blocks = [block('second')]
        region.publish_draft()

        restored = region.restore_revision(first)

        self.assertEqual(restored.number, 3)
        self.assertEqual(
            region.live_revision_id.blocks[0]['data']['title']['en_US'], 'first')


@tagged('post_install', '-at_install')
class TestCmsSeo(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['res.lang']._activate_lang('pt_PT')
        cls.Page = cls.env['alokai.website.page']
        cls.page = cls.Page.create({'name': 'About', 'url': '/seo-about'})

    def test_seo_is_saved_per_language(self):
        self.page.set_seo('en_US', {'title': 'About us', 'description': 'Who we are'})
        self.page.set_seo('pt_PT', {'title': 'Sobre nós'})

        seo = self.page.get_seo()
        self.assertEqual(seo['source'], 'page')
        self.assertEqual(seo['title'], {'en_US': 'About us', 'pt_PT': 'Sobre nós'})
        # Never written in Portuguese, so it is missing rather than English.
        self.assertNotIn('pt_PT', seo['description'])

    def test_clearing_one_language_keeps_the_others(self):
        self.page.set_seo('en_US', {'title': 'About us'})
        self.page.set_seo('pt_PT', {'title': 'Sobre nós'})

        self.page.set_seo('pt_PT', {'title': ''})

        self.assertEqual(self.page.get_seo()['title'], {'en_US': 'About us'})

    def test_homepage_seo_lives_on_the_website(self):
        """The homepage renders the website record's tags, not its page's."""
        home = self.Page.seed_homepage([block()])
        home.set_seo('en_US', {'title': 'Shop timeless style'})

        self.assertEqual(home.get_seo()['source'], 'website')
        self.assertEqual(home.website_id.website_meta_title, 'Shop timeless style')
        self.assertFalse(home.website_meta_title)

    def test_share_image_is_saved_and_removed(self):
        # 1x1 transparent PNG.
        png = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAA'
               'AAYAAjCB0C8AAAAASUVORK5CYII=')
        self.page.set_seo('en_US', {'image': png})

        url = self.page.get_seo()['image']
        self.assertTrue(url.startswith(
            '/web/image/alokai.website.page/%s/website_meta_img' % self.page.id))

        self.page.set_seo('en_US', {'image': ''})
        self.assertIsNone(self.page.get_seo()['image'])

    def test_a_region_has_no_seo(self):
        region = self.Page.create({
            'name': 'Below products', 'kind': 'region', 'region_key': 'seo-region'})
        with self.assertRaises(UserError):
            region.set_seo('en_US', {'title': 'Nope'})


@tagged('post_install', '-at_install')
class TestCmsInvalidation(TransactionCase):
    """Cache invalidation in both directions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param(
            'alokai_cache_invalidation', '1')
        cls.Page = cls.env['alokai.website.page']
        cls.product = cls.env['product.template'].create({
            'name': 'Featured Thing', 'is_published': True,
        })

    def _queued_page_ids(self):
        self.env.cr.execute(
            "SELECT res_id FROM invalidate_cache WHERE res_model = %s",
            ('alokai.website.page',))
        return [row[0] for row in self.env.cr.fetchall()]

    def _clear_queue(self):
        self.env.cr.execute(
            "DELETE FROM invalidate_cache WHERE res_model = %s",
            ('alokai.website.page',))

    def test_publishing_queues_the_page(self):
        page = self.Page.create({'name': 'Q', 'url': '/queued'})
        self._clear_queue()

        page.draft_blocks = [block()]
        page.publish_draft()

        self.assertIn(page.id, self._queued_page_ids())

    def test_draft_only_writes_queue_nothing(self):
        """Autosave fires about once a second; evicting the cache each time
        would be worse than useless."""
        page = self.Page.create({'name': 'D', 'url': '/draft-only'})
        self._clear_queue()

        page.write({'draft_blocks': [block('typing')]})

        self.assertEqual(self._queued_page_ids(), [])

    def test_product_write_invalidates_featuring_pages(self):
        """Regression: the hook was silently replaced by a later `write`."""
        page = self.Page.create({'name': 'F', 'url': '/features-product'})
        page.draft_blocks = [block(block_type='featuredProducts')]
        page.publish_draft(references={'product_tmpl_ids': self.product.ids})
        self._clear_queue()

        self.product.write({'description_sale': 'changed'})

        self.assertIn(page.id, self._queued_page_ids())

    def test_product_write_ignores_pages_referencing_it_only_in_old_revisions(self):
        """A page whose OLD revision mentioned a product shows something else
        now; refreshing it would act on content nobody can see."""
        page = self.Page.create({'name': 'S', 'url': '/stale-reference'})
        page.draft_blocks = [block(block_type='featuredProducts')]
        page.publish_draft(references={'product_tmpl_ids': self.product.ids})

        page.draft_blocks = [block('no products now')]
        page.publish_draft(references={})
        self._clear_queue()

        self.product.write({'description_sale': 'changed again'})

        self.assertNotIn(page.id, self._queued_page_ids())

    def test_unpublished_pages_are_not_invalidated_by_product_changes(self):
        page = self.Page.create({'name': 'U', 'url': '/unpublished'})
        page.draft_blocks = [block()]
        page.publish_draft(references={'product_tmpl_ids': self.product.ids})
        page.unpublish_page()
        self._clear_queue()

        self.product.write({'description_sale': 'once more'})

        self.assertNotIn(page.id, self._queued_page_ids())


@tagged('post_install', '-at_install')
class TestCmsSecurity(TransactionCase):
    """Who may read and write content."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.editor = new_test_user(
            cls.env, login='cms_editor_test',
            groups='base.group_user,graphql_alokai.group_cms_editor')
        cls.plain_user = new_test_user(
            cls.env, login='cms_plain_test', groups='base.group_user')
        cls.page = cls.env['alokai.website.page'].create({
            'name': 'Secured', 'url': '/secured'})

    def test_editor_can_write_content(self):
        page = self.page.with_user(self.editor)
        page.draft_blocks = [block('by an editor')]
        self.assertTrue(page.draft_blocks)

    def test_internal_user_without_the_group_cannot_write(self):
        page = self.page.with_user(self.plain_user)
        with self.assertRaises(AccessError):
            page.draft_blocks = [block('by a non-editor')]

    def test_internal_user_without_the_group_cannot_create_revisions(self):
        with self.assertRaises(AccessError):
            self.env['alokai.page.revision'].with_user(self.plain_user).create({
                'page_id': self.page.id, 'number': 1, 'blocks': [],
            })

    def test_public_user_cannot_write(self):
        public = self.env.ref('base.public_user')
        with self.assertRaises(AccessError):
            self.page.with_user(public).draft_blocks = [block('by the public')]

    def test_admin_is_an_editor_on_install(self):
        """A fresh install needs at least one working editor."""
        admin = self.env.ref('base.user_admin')
        self.assertTrue(admin.has_group('graphql_alokai.group_cms_editor'))


@tagged('post_install', '-at_install')
class TestCmsGraphQL(AlokaiGraphQLCommon):
    """The GraphQL surface, over HTTP, as a client actually calls it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.editor_password = 'cms_editor_pw'
        cls.editor = new_test_user(
            cls.env, login='cms_gql_editor',
            password=cls.editor_password,
            groups='base.group_user,graphql_alokai.group_cms_editor')

        cls.published = cls.env['alokai.website.page'].create({
            'name': 'Public Page', 'url': '/public-page'})
        cls.published.draft_blocks = [block('public copy')]
        cls.published.publish_draft()

        cls.hidden = cls.env['alokai.website.page'].create({
            'name': 'Hidden Page', 'url': '/hidden-page'})
        cls.hidden.draft_blocks = [block('secret draft')]

    # --- public reads -----------------------------------------------------

    def test_published_page_is_readable_anonymously(self):
        body = self._gql(
            'query ($slug: String) { cmsPage(slug: $slug) { id name blocks } }',
            {'slug': '/public-page'})
        page = body['data']['cmsPage']

        self.assertEqual(page['name'], 'Public Page')
        self.assertEqual(
            page['blocks'][0]['data']['title']['en_US'], 'public copy')

    def test_unpublished_page_is_not_readable(self):
        body = self._gql(
            'query ($slug: String) { cmsPage(slug: $slug) { id } }',
            {'slug': '/hidden-page'})
        self.assertIsNone(body['data']['cmsPage'])

    def test_unknown_slug_returns_null(self):
        body = self._gql(
            'query ($slug: String) { cmsPage(slug: $slug) { id } }',
            {'slug': '/no-such-page'})
        self.assertIsNone(body['data']['cmsPage'])

    def test_draft_never_leaks_to_the_public_read(self):
        """The guarantee the whole draft/published split exists for."""
        self.published.draft_blocks = [block('UNPUBLISHED EDIT')]

        body = self._gql(
            'query ($slug: String) { cmsPage(slug: $slug) { blocks } }',
            {'slug': '/public-page'})

        serialised = str(body['data']['cmsPage']['blocks'])
        self.assertNotIn('UNPUBLISHED EDIT', serialised)
        self.assertIn('public copy', serialised)

    # --- authorisation ----------------------------------------------------

    def test_anonymous_cannot_read_drafts_or_lists(self):
        for document in (
            '{ cmsPages { totalCount } }',
            'query ($id: Int!) { cmsPageDraft(id: $id) { id } }',
            'query ($id: Int!) { cmsRevisions(pageId: $id) { number } }',
            '{ cmsProducts(limit: 1) { id } }',
            '{ cmsCategories(limit: 1) { id } }',
        ):
            body = self._gql(
                document, {'id': self.published.id}, expect_errors=True)
            self.assertTrue(body['errors'], document)

    def test_anonymous_cannot_write(self):
        for document, variables in (
            ('mutation { createCmsPage(name: "X", url: "/x") { id } }', {}),
            ('mutation ($id: Int!, $b: GenericScalar!) '
             '{ saveCmsDraft(pageId: $id, blocks: $b) { id } }',
             {'id': self.published.id, 'b': []}),
            ('mutation ($id: Int!) { publishCmsPage(pageId: $id) { id } }',
             {'id': self.published.id}),
            ('mutation ($id: Int!) { unpublishCmsPage(pageId: $id) { id } }',
             {'id': self.published.id}),
            ('mutation ($id: Int!) { deleteCmsPage(pageId: $id) }',
             {'id': self.published.id}),
        ):
            body = self._gql(document, variables, expect_errors=True)
            self.assertTrue(body['errors'], document)

    def test_cms_can_edit_is_false_anonymously(self):
        body = self._gql('{ cmsCanEdit }')
        self.assertFalse(body['data']['cmsCanEdit'])

    def test_cms_can_edit_is_true_for_an_editor(self):
        self.authenticate(self.editor.login, self.editor_password)
        body = self._gql('{ cmsCanEdit }')
        self.assertTrue(body['data']['cmsCanEdit'])

    # --- editor flows -----------------------------------------------------

    def test_editor_can_run_the_whole_lifecycle(self):
        self.authenticate(self.editor.login, self.editor_password)

        created = self._gql(
            'mutation { createCmsPage(name: "Lifecycle", url: "/lifecycle") '
            '{ id isPublished } }')['data']['createCmsPage']
        self.assertFalse(created['isPublished'])
        page_id = created['id']

        saved = self._gql(
            'mutation ($id: Int!, $b: GenericScalar!) '
            '{ saveCmsDraft(pageId: $id, blocks: $b) '
            '{ hasUnpublishedChanges } }',
            {'id': page_id, 'b': [block('draft copy')]},
        )['data']['saveCmsDraft']
        self.assertTrue(saved['hasUnpublishedChanges'])

        published = self._gql(
            'mutation ($id: Int!) { publishCmsPage(pageId: $id) '
            '{ isPublished liveRevision hasUnpublishedChanges } }',
            {'id': page_id})['data']['publishCmsPage']
        self.assertTrue(published['isPublished'])
        self.assertEqual(published['liveRevision'], 1)
        self.assertFalse(published['hasUnpublishedChanges'])

        self.assertTrue(
            self._gql('mutation ($id: Int!) { deleteCmsPage(pageId: $id) }',
                      {'id': page_id})['data']['deleteCmsPage'])

    def test_duplicate_url_is_rejected(self):
        self.authenticate(self.editor.login, self.editor_password)
        body = self._gql(
            'mutation { createCmsPage(name: "Clash", url: "/public-page") '
            '{ id } }', expect_errors=True)
        self.assertTrue(body['errors'])

    def test_a_category_url_is_rejected(self):
        """The category route would win, so the page could never be seen."""
        self.env['product.public.category'].create({
            'name': 'Clash Category', 'website_slug': '/clash-category'})
        self.authenticate(self.editor.login, self.editor_password)

        body = self._gql(
            'mutation { createCmsPage(name: "Clash", url: "/clash-category") '
            '{ id } }', expect_errors=True)
        self.assertIn('Clash Category', body['errors'][0]['message'])

        page = self.env['alokai.website.page'].create({
            'name': 'Movable', 'url': '/movable'})
        body = self._gql(
            'mutation ($id: Int!) { updateCmsPage(pageId: $id, '
            'url: "/clash-category") { id } }',
            {'id': page.id}, expect_errors=True)
        self.assertTrue(body['errors'])
        self.assertEqual(page.url, '/movable')

    def test_editor_saves_and_reads_seo(self):
        page = self.env['alokai.website.page'].create({
            'name': 'Seo', 'url': '/seo-gql'})
        self.authenticate(self.editor.login, self.editor_password)

        self._gql(
            'mutation ($id: Int!) { updateCmsPageSeo(pageId: $id, lang: "en_US", '
            'metaTitle: "Our story", metaDescription: "How it started") { id } }',
            {'id': page.id})
        body = self._gql(
            'query ($id: Int!) { cmsPageDraft(id: $id) { seo } }', {'id': page.id})

        seo = body['data']['cmsPageDraft']['seo']
        self.assertEqual(seo['title']['en_US'], 'Our story')
        self.assertEqual(seo['description']['en_US'], 'How it started')

    def test_malformed_url_is_rejected(self):
        self.authenticate(self.editor.login, self.editor_password)
        for url in ('no-leading-slash', '/', ''):
            body = self._gql(
                'mutation ($u: String!) '
                '{ createCmsPage(name: "Bad", url: $u) { id } }',
                {'u': url}, expect_errors=True)
            self.assertTrue(body['errors'], url)

    def test_restore_through_graphql(self):
        self.authenticate(self.editor.login, self.editor_password)
        self.env['ir.config_parameter'].sudo().set_param(
            'alokai_cms_revision_limit', '10')

        page = self.env['alokai.website.page'].create({
            'name': 'Rollback', 'url': '/rollback'})
        page.draft_blocks = [block('version one')]
        first = page.publish_draft()
        page.draft_blocks = [block('version two')]
        page.publish_draft()

        revisions = self._gql(
            'query ($id: Int!) { cmsRevisions(pageId: $id) '
            '{ id number isLive author } }',
            {'id': page.id})['data']['cmsRevisions']
        self.assertEqual(len(revisions), 2)
        self.assertTrue(any(r['isLive'] for r in revisions))
        self.assertTrue(all(r['author'] for r in revisions))

        restored = self._gql(
            'mutation ($p: Int!, $r: Int!) '
            '{ restoreCmsRevision(pageId: $p, revisionId: $r) '
            '{ liveRevision blocks } }',
            {'p': page.id, 'r': first.id})['data']['restoreCmsRevision']

        self.assertEqual(restored['liveRevision'], 3)
        self.assertEqual(
            restored['blocks'][0]['data']['title']['en_US'], 'version one')

    def test_pickers_return_real_records(self):
        self.authenticate(self.editor.login, self.editor_password)
        self.env['product.template'].create({
            'name': 'Picker Product', 'is_published': True})

        products = self._gql(
            'query ($s: String) { cmsProducts(search: $s, limit: 5) '
            '{ id name imageUrl } }',
            {'s': 'Picker'})['data']['cmsProducts']

        self.assertTrue(products)
        self.assertIn('Picker Product', [p['name'] for p in products])
        self.assertTrue(products[0]['imageUrl'])

    def test_picker_resolves_ids_regardless_of_search(self):
        """The inspector must show what is already selected even when it does
        not match the search box."""
        self.authenticate(self.editor.login, self.editor_password)
        product = self.env['product.template'].create({
            'name': 'Zzz Selected', 'is_published': True})

        products = self._gql(
            'query ($ids: [Int]) { cmsProducts(ids: $ids) { id name } }',
            {'ids': [product.id]})['data']['cmsProducts']

        self.assertEqual([p['id'] for p in products], [product.id])

    def test_page_list_reports_real_block_counts(self):
        """Regression: the list showed "0 blocks" for every page.

        The list query deliberately does not fetch block bodies - that is what
        keeps it small - so the storefront was deriving the count from data it
        had never asked for and reporting every page as empty. The count comes
        from the backend, which can see them.
        """
        self.authenticate(self.editor.login, self.editor_password)

        page = self.env['alokai.website.page'].create({
            'name': 'Counted', 'url': '/counted'})
        page.draft_blocks = [block('one'), block('two'), block('three')]

        pages = self._gql(
            '{ cmsPages { pages { url blockCount hasUnpublishedChanges } } }'
        )['data']['cmsPages']['pages']

        row = next(p for p in pages if p['url'] == '/counted')
        self.assertEqual(row['blockCount'], 3)
        # Never published, so the draft differs from (empty) live content.
        self.assertTrue(row['hasUnpublishedChanges'])

    def test_unpublished_changes_is_reported_on_the_list(self):
        self.authenticate(self.editor.login, self.editor_password)

        page = self.env['alokai.website.page'].create({
            'name': 'Synced', 'url': '/synced'})
        page.draft_blocks = [block('live copy')]
        page.publish_draft()

        pages = self._gql(
            '{ cmsPages { pages { url hasUnpublishedChanges blockCount } } }'
        )['data']['cmsPages']['pages']
        row = next(p for p in pages if p['url'] == '/synced')
        self.assertFalse(row['hasUnpublishedChanges'])
        self.assertEqual(row['blockCount'], 1)

        page.draft_blocks = [block('edited')]
        pages = self._gql(
            '{ cmsPages { pages { url hasUnpublishedChanges } } }'
        )['data']['cmsPages']['pages']
        row = next(p for p in pages if p['url'] == '/synced')
        self.assertTrue(row['hasUnpublishedChanges'])

    def test_block_count_is_zero_for_an_empty_page(self):
        self.authenticate(self.editor.login, self.editor_password)
        self.env['alokai.website.page'].create({'name': 'Blank', 'url': '/blank'})

        pages = self._gql(
            '{ cmsPages { pages { url blockCount } } }'
        )['data']['cmsPages']['pages']
        row = next(p for p in pages if p['url'] == '/blank')
        self.assertEqual(row['blockCount'], 0)

    def test_region_blocks_are_readable_anonymously(self):
        region = self.env['alokai.website.page'].seed_region(
            'gql-slot', 'A slot')
        region.draft_blocks = [block('region copy')]
        region.publish_draft()

        body = self._gql(
            'query ($k: String!) { cmsRegion(key: $k) { id blocks } }',
            {'k': 'gql-slot'})

        self.assertEqual(
            body['data']['cmsRegion']['blocks'][0]['data']['title']['en_US'],
            'region copy')

    def test_an_unpublished_region_reads_as_nothing(self):
        """An empty slot must render nothing rather than erroring."""
        self.env['alokai.website.page'].seed_region('gql-empty', 'Empty slot')

        body = self._gql(
            'query ($k: String!) { cmsRegion(key: $k) { id } }',
            {'k': 'gql-empty'})

        self.assertIsNone(body['data']['cmsRegion'])

    def test_an_unknown_region_reads_as_nothing(self):
        body = self._gql(
            'query ($k: String!) { cmsRegion(key: $k) { id } }',
            {'k': 'no-such-slot'})
        self.assertIsNone(body['data']['cmsRegion'])

    def test_a_system_page_cannot_be_deleted_over_graphql(self):
        self.authenticate(self.editor.login, self.editor_password)
        page = self.env['alokai.website.page'].create({
            'name': 'Built in', 'url': '/built-in', 'is_system': True})

        body = self._gql(
            'mutation ($id: Int!) { deleteCmsPage(pageId: $id) }',
            {'id': page.id}, expect_errors=True)

        self.assertTrue(body['errors'])
        self.assertTrue(page.exists())

    def test_locales_come_from_odoo(self):
        body = self._gql('{ cmsLocales { code label isDefault } }')
        locales = body['data']['cmsLocales']

        self.assertTrue(locales)
        self.assertEqual(sum(1 for l in locales if l['isDefault']), 1)

    def test_website_page_type_exposes_blocks(self):
        body = self._gql(
            'query ($slug: String) { websitePage(pageSlug: $slug) '
            '{ id blocks } }',
            {'slug': '/public-page'})
        self.assertEqual(
            body['data']['websitePage']['blocks'][0]['data']['title']['en_US'],
            'public copy')
