# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

from .alokai_page_revision import pretty_blocks, validate_blocks_structure


class AlokaiWebsitePage(models.Model):
    _name = 'alokai.website.page'
    _inherit = [
        'website.published.multi.mixin',
        'website.searchable.mixin',
        # Gives the page website_meta_title / website_meta_description, which
        # the CMS render target emits. Odoo's own SEO fields rather than our
        # own, so they behave like every other page's in the backend.
        'website.seo.metadata',
    ]
    _description = 'Alokai Website Page'
    _order = 'website_id'

    def _default_content(self):
        return '<p class="o_default_snippet_text">' + _("Start writing here...") + '</p>'

    name = fields.Char(string='Page Name', translate=True, required=True)
    url = fields.Char(string='Page URL', translate=True)
    website_id = fields.Many2one('website', string="Website")
    date_publish = fields.Datetime('Publishing Date')
    content = fields.Text(string='Content', default=_default_content, translate=True)

    page_type = fields.Selection(selection=[
        ('static', 'Static Page'), ('products', 'Campaign Page')
    ], string='Page Type', default='static', required=True)

    product_tmpl_ids = fields.Many2many(
        'product.template', 'product_template_alokai_website_page_rel', 'alokai_page_id', 'product_tmpl_id',
        string='Product Templates'
    )

    # --- CMS -------------------------------------------------------------
    # Content is authored as blocks in the storefront CMS editor. `content` and
    # `page_type` above are superseded by this and kept only so existing
    # installs keep working; they are removed in a later, separate migration.
    # `product_tmpl_ids` is likewise superseded by the mirrored references on
    # each revision.

    # --- what this record is ---------------------------------------------
    # A `page` owns a url and everything on it. A `region` is a named slot the
    # storefront places inside a page it owns - under the product listing, for
    # instance - so a merchant can add content to pages whose structure is
    # full of business logic they must not be able to rearrange.
    #
    # Both share this model so drafts, revisions, publishing, validation and
    # the whole editor work for them without a second content pipeline.
    kind = fields.Selection(
        selection=[('page', 'Page'), ('region', 'Region')],
        string='Kind', default='page', required=True, index=True,
    )
    region_key = fields.Char(
        string='Region Key', index=True,
        help='Which slot in the storefront this content fills, for example '
             'category-after. Set by the storefront, not by the merchant.',
    )

    is_system = fields.Boolean(
        string='System Page',
        default=False,
        help='A page the storefront owns, such as the homepage. Its content '
             'is editable but it cannot be deleted and its address is fixed.',
    )

    draft_blocks = fields.Json(
        string='Draft Content',
        help='What the editor is working on. Never served to visitors.',
    )
    draft_attachment_ids = fields.Many2many(
        'ir.attachment',
        'alokai_page_draft_attachment_rel',
        'page_id',
        'attachment_id',
        string='Draft Images',
        help='Keeps images used only by the draft reachable by the ORM.',
    )

    revision_ids = fields.One2many(
        'alokai.page.revision', 'page_id', string='Revisions',
    )
    live_revision_id = fields.Many2one(
        'alokai.page.revision',
        string='Live Revision',
        ondelete='restrict',
        copy=False,
        help='The revision visitors currently see.',
    )
    revision_count = fields.Integer(compute='_compute_revision_count')
    live_revision_number = fields.Integer(
        related='live_revision_id.number', string='Live Revision No.')

    # For the debug-mode backend views only: the web client has no widget
    # for Json fields, so the blocks are shown as indented text.
    draft_blocks_display = fields.Text(
        string='Draft Blocks (JSON)', compute='_compute_blocks_display')
    live_blocks_display = fields.Text(
        string='Live Blocks (JSON)', compute='_compute_blocks_display')

    @api.depends('draft_blocks', 'live_revision_id.blocks')
    def _compute_blocks_display(self):
        for page in self:
            page.draft_blocks_display = pretty_blocks(page.draft_blocks)
            page.live_blocks_display = pretty_blocks(page.live_revision_id.blocks)

    @api.depends('revision_ids')
    def _compute_revision_count(self):
        for page in self:
            page.revision_count = len(page.revision_ids)

    _region_key_uniq = models.Constraint(
        'UNIQUE (region_key, website_id)',
        'A website can only have one block list per region.',
    )

    @api.constrains('kind', 'url', 'region_key')
    def _check_kind(self):
        for page in self:
            if page.kind == 'page' and not page.url:
                raise ValidationError(_('A page needs an address.'))
            if page.kind == 'region' and not page.region_key:
                raise ValidationError(_('A region needs a key.'))

    @api.model
    def seed_region(self, region_key, name):
        """Make sure a region exists so the merchant can find and fill it.

        Regions are declared by the storefront, not created by merchants -
        there is nowhere to put content that the storefront has not placed a
        slot for, which is the guarantee that page structure stays in code.

        Idempotent, and deliberately starts empty: an unfilled region renders
        nothing, so a fresh install looks exactly as it does today.
        """
        website = self.env['website'].get_current_website()
        existing = self.search([
            ('region_key', '=', region_key),
            ('website_id', 'in', (False, website.id)),
        ], limit=1)
        if existing:
            return existing

        return self.create({
            'name': name,
            'kind': 'region',
            'region_key': region_key,
            'website_id': website.id,
            'is_system': True,
            'draft_blocks': [],
        })

    @api.constrains('draft_blocks')
    def _check_draft_blocks(self):
        # Structural backstop only. The storefront validates against the block
        # schema before it ever gets here - Odoo does not know what a block is.
        for page in self:
            validate_blocks_structure(page.draft_blocks)

    # --- publishing ------------------------------------------------------

    def save_draft(self, blocks, expected_write_date=None):
        """Replace the whole draft document.

        Whole-document drafts mean two editors on one page would silently
        clobber each other, so the caller passes the write_date it last saw and
        gets told to reload rather than losing someone's work.
        """
        self.ensure_one()

        if expected_write_date and self.write_date:
            seen = fields.Datetime.to_datetime(expected_write_date)
            # Second precision: the client round-trips this through JSON, and
            # microseconds do not survive the trip intact.
            if seen and abs((self.write_date - seen).total_seconds()) >= 1:
                raise UserError(_(
                    'Someone else changed this page while you were editing. '
                    'Reload to see their changes before saving yours.'
                ))

        self.write({'draft_blocks': blocks})
        return self

    def publish_draft(self, references=None):
        """Copy the draft into a new revision and make it live.

        `references` carries the product, category and attachment ids the
        storefront found inside the blocks. Odoo cannot extract them itself -
        the blocks column is opaque to it - so they are mirrored in by the only
        layer that understands the content.
        """
        self.ensure_one()
        return self._create_revision(
            self.draft_blocks or [],
            references=references,
        )

    def restore_revision(self, revision):
        """Bring an older revision back by COPYING IT FORWARD.

        Not by moving the live pointer backwards. History stays append-only, so
        "what was live on 3 March?" always has an answer, and the live revision
        is always the newest, which is why pruning needs no exception for it.

        The draft is reset to match: in almost every case it holds exactly what
        is being rolled back FROM, so leaving it would show "unpublished
        changes" pointing at the rejected content. The caller warns first if
        the draft differs.
        """
        self.ensure_one()

        if revision.page_id != self:
            raise UserError(_('That revision belongs to a different page.'))

        # Read everything BEFORE creating the new revision: creating one
        # prunes, and with a small revision limit the source revision is itself
        # a candidate for deletion. Its content is not lost - that is the point
        # of copying forward - but the record may be gone by the next line.
        source_blocks = revision.blocks or []
        source_number = revision.number
        references = {
            'product_tmpl_ids': revision.product_tmpl_ids.ids,
            'category_ids': revision.category_ids.ids,
            'attachment_ids': revision.attachment_ids.ids,
        }

        new_revision = self._create_revision(
            source_blocks,
            references=references,
            restored_from=revision,
            restored_from_number=source_number,
        )
        self.draft_blocks = source_blocks
        return new_revision

    def _create_revision(self, blocks, references=None, restored_from=None,
                         restored_from_number=None):
        self.ensure_one()
        validate_blocks_structure(blocks)

        Revision = self.env['alokai.page.revision']
        last = Revision.search(
            [('page_id', '=', self.id)], order='number desc', limit=1,
        )

        revision = Revision.create({
            'page_id': self.id,
            'number': (last.number if last else 0) + 1,
            'blocks': blocks,
            'restored_from_id': restored_from.id if restored_from else False,
            'restored_from_number': restored_from_number or 0,
        })

        # Written immediately after create, in the same transaction - the only
        # mutation a revision ever accepts.
        refs = references or {}
        revision.write({
            'product_tmpl_ids': [(6, 0, refs.get('product_tmpl_ids') or [])],
            'category_ids': [(6, 0, refs.get('category_ids') or [])],
            'attachment_ids': [(6, 0, refs.get('attachment_ids') or [])],
        })

        self.write({
            'live_revision_id': revision.id,
            'is_published': True,
        })

        # Pruning runs after the live pointer moves, so the revision being
        # kept is never a candidate for deletion.
        Revision._prune(self)
        return revision

    def discard_draft(self):
        """Reset the draft to whatever is live. The merchant's undo-everything."""
        self.ensure_one()
        self.draft_blocks = (
            self.live_revision_id.blocks if self.live_revision_id else []
        )
        return self

    def unpublish_page(self):
        self.ensure_one()
        self.is_published = False
        return self

    # --- system pages -----------------------------------------------------

    @api.ondelete(at_uninstall=False)
    def _unlink_except_system(self):
        """The homepage is a route the storefront owns.

        Its content is the merchant's; its existence is not. Deleting it would
        leave / resolving to nothing, so the guard lives here rather than in
        the UI - hiding the button is a courtesy, this is the rule.
        """
        for page in self:
            if page.is_system:
                raise UserError(_(
                    '"%s" is part of the storefront and cannot be deleted. '
                    'You can change the content on it.'
                ) % page.name)

    @api.model
    def seed_homepage(self, blocks, name='Homepage'):
        """Create or update the system page that backs /.

        Idempotent, so it can be re-run after a storefront release adds blocks
        without clobbering what the merchant has written: an existing page is
        left exactly as it is.
        """
        website = self.env['website'].get_current_website()
        existing = self.search([
            ('url', '=', '/'),
            ('website_id', 'in', (False, website.id)),
        ], limit=1)

        if existing:
            existing.is_system = True
            return existing

        page = self.create({
            'name': name,
            'url': '/',
            'website_id': website.id,
            'is_system': True,
            'draft_blocks': blocks,
        })
        page.publish_draft()
        return page

    # --- SEO ---------------------------------------------------------------

    _SEO_FIELDS = {
        'title': 'website_meta_title',
        'description': 'website_meta_description',
    }

    def _seo_record(self):
        """The record whose SEO the storefront actually renders for this page.

        The homepage takes its tags from the website record, not from its CMS
        page (see layers/home/pages/index.vue in the storefront), so editing
        the page's own fields would change nothing a visitor or crawler sees.
        sudo for the website because a CMS editor is not a website admin; the
        caller only ever writes the two meta fields through it.
        """
        self.ensure_one()
        if self.is_system and self.url == '/':
            website = self.website_id or self.env['website'].get_current_website()
            return website.sudo()
        return self

    def get_seo(self):
        """Stored meta title and description per language.

        Raw stored values rather than reads in each language, because a read
        falls back to English and would make an untranslated field look done.
        A language missing from a map has no translation of its own.
        """
        record = self._seo_record()
        result = {
            'source': 'website' if record._name == 'website' else 'page',
            'image': self._seo_image_url(),
        }
        for key, field_name in self._SEO_FIELDS.items():
            stored = record._fields[field_name]._get_stored_translations(record)
            result[key] = stored or {}
        return result

    def _seo_image_url(self):
        """Odoo-relative URL of the share image, or None.

        Not translated: a picture rarely differs by language, and Odoo's image
        field cannot be. The write date busts browser and CDN caches when the
        image is replaced under the same URL.
        """
        record = self._seo_record()
        if not record.website_meta_img:
            return None
        unique = int(record.write_date.timestamp()) if record.write_date else 0
        return '/web/image/%s/%s/website_meta_img?unique=%s' % (
            record._name, record.id, unique)

    def set_seo(self, lang, values):
        """Save meta title and description for one language.

        Goes through update_field_translations rather than write(): clearing a
        field with write() in one language would wipe it in every language.
        Here an empty value removes only that language's text, which then
        falls back to English the way every other translated field does.
        """
        self.ensure_one()
        if self.kind != 'page':
            raise UserError(_('Only pages have SEO settings.'))

        record = self._seo_record()
        for key, field_name in self._SEO_FIELDS.items():
            if key not in values:
                continue
            value = (values[key] or '').strip()
            # English is the base every other language falls back to, so it
            # is emptied rather than removed.
            if not value:
                value = '' if lang == 'en_US' else False
            record.update_field_translations(field_name, {lang: value})

        # Base64 image data, or a falsy value to remove the image.
        if 'image' in values:
            record.website_meta_img = values['image'] or False

        self._invalidate_storefront_cache()
        return self

    # --- cache invalidation ----------------------------------------------

    def _invalidate_storefront_cache(self):
        """Queue this page's URL for invalidation.

        Without this, a merchant publishes, reloads, sees the old page and
        reports it as a bug. Treat it as part of publishing, not a follow-up.
        """
        if not self:
            return
        self.env['invalidate.cache'].create_invalidate_cache(
            'alokai.website.page', self.ids,
        )

    @api.model_create_multi
    def create(self, vals_list):
        pages = super().create(vals_list)
        pages._invalidate_storefront_cache()
        return pages

    def write(self, vals):
        # One write for the whole model. A second definition further down the
        # class body silently replaces an earlier one, which is how the url
        # guard below spent its first outing never running at all.
        if 'url' in vals:
            for page in self:
                if page.is_system and vals['url'] != page.url:
                    raise UserError(_(
                        'The address of "%s" is fixed by the storefront and '
                        'cannot be changed.'
                    ) % page.name)
        # Unpublishing would leave the storefront route with nothing behind
        # it, the same as deleting, so it gets the same guard.
        if 'is_published' in vals and not vals['is_published']:
            for page in self:
                if page.is_system and page.is_published:
                    raise UserError(_(
                        '"%s" is part of the storefront and cannot be '
                        'unpublished.'
                    ) % page.name)

        result = super().write(vals)

        # Draft edits change nothing a visitor can see, so invalidating on them
        # would evict the cache on every autosave - roughly once a second while
        # someone is typing.
        visitor_facing = set(vals) - {'draft_blocks', 'draft_attachment_ids'}
        if visitor_facing:
            self._invalidate_storefront_cache()
        return result

    def unlink(self):
        pages = self.exists()
        pages._invalidate_storefront_cache()
        return super(AlokaiWebsitePage, pages).unlink()
