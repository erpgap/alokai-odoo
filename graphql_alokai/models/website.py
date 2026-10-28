# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import re
import hashlib
import hmac
import logging
import redis
import requests
import pprint
import json
import time
import urllib.parse
import uuid
from collections import defaultdict
from html import unescape
from odoo import models, fields, api, tools
from odoo.tools import split_every
from odoo import _
from odoo.addons.graphql_alokai.schemas.objects import get_image_url
from odoo.fields import Domain
from odoo.exceptions import UserError
from redis.exceptions import TimeoutError, AuthenticationError, ConnectionError

_logger = logging.getLogger(__name__)

# Records per dirty entry, and per request to the Zeppln storefront.
DIRTY_BATCH = 1000

# Payment return URL field: (the field editing its path, the default path).
PAYMENT_RETURN_FIELDS = {
    'alokai_payment_success_return_url': ('alokai_payment_success_path', '/checkout/thank-you'),
    'alokai_payment_error_return_url': ('alokai_payment_error_path', '/checkout/payment-fail'),
}

# Every model with slug keys, for the full resync.
SLUG_MODELS = (
    'product.template', 'product.public.category',
    'blog.tag', 'blog.blog', 'blog.post', 'alokai.website.page',
)


def slug_key(slug):
    # The storefront looks keys up with encodeURIComponent.
    return f"slug:{urllib.parse.quote(slug, safe='')}"


def sync_slug_keys(redis_client, model_name, current, gone):
    """Set a key for every slug in `current` and drop those in `gone`.

    The storefront resolves every URL its build did not know through these
    keys and answers 404 when there is none, without asking Odoo. A key left
    behind sends visitors to a page that asks Odoo only to find nothing.

    A key now owned by another model is not the caller's to remove.
    """
    for slug in set(gone) - set(current):
        key = slug_key(slug)
        if redis_client.get(key) == model_name:
            redis_client.delete(key)

    pipe = redis_client.pipeline()
    for slug in current:
        pipe.set(slug_key(slug), model_name)
    pipe.execute()


class WebsiteSlugRedisMixin(models.AbstractModel):
    _name = 'website.slug.redis.mixin'
    _description = 'Mixin to sync website slugs with Redis'

    def _redis_slug_is_live(self):
        """Whether the storefront should route to this record at all."""
        self.ensure_one()
        if 'active' in self._fields and not self.active:
            return False
        if hasattr(self, 'is_published') and not self.is_published:
            return False
        if hasattr(self, 'sale_ok') and not self.sale_ok:
            return False
        return True

    def _redis_slugs(self, live_only=True):
        """Every slug these records have, in every language."""
        langs = self.env['res.lang'].search([])
        return {
            slug
            for record in self
            if not live_only or record._redis_slug_is_live()
            for lang in langs
            for slug in [record.with_context(lang=lang.code).website_slug]
            if slug
        }

    def _slug_changes(self, previous=frozenset()):
        """``(current, gone)``: the slugs these records answer at now, and
        those of `previous` - the slugs they had before the change - they no
        longer do: unpublished, archived, renamed, deleted."""
        current = self._redis_slugs()
        gone = set(previous) - current
        if gone:
            # Translations can make two records share a slug.
            others = self.search([
                ('id', 'not in', self.ids),
                ('website_slug', 'in', list(gone)),
            ])
            gone -= others._redis_slugs()
        return current, gone

    def _update_slug_in_redis(self, previous=frozenset()):
        """Make the slug keys match these records as they are now, at once.
        Saves go through the dirty cron instead (_alokai_mark_slugs_dirty)."""
        redis_client = self.env['website']._redis_connect()
        if not redis_client:
            return
        try:
            sync_slug_keys(redis_client, self._name, *self._slug_changes(previous))
        finally:
            redis_client.close()

    def _redis_previous_slugs(self):
        # Read only when the keys will be synced: a write with nowhere to
        # send slugs should not pay for reading every language's slug.
        if not self.env['website']._alokai_sync_mode():
            return frozenset()
        return self._redis_slugs(live_only=False)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env['website']._alokai_mark_slugs_dirty(records)
        return records

    def write(self, vals):
        previous = self._redis_previous_slugs()
        res = super().write(vals)
        self.env['website']._alokai_mark_slugs_dirty(self, previous)
        return res

    def unlink(self):
        previous = self._redis_previous_slugs()
        res = super().unlink()
        self.env['website']._alokai_mark_slugs_dirty(self, previous)
        return res


class WebsiteSeoMetadata(models.AbstractModel):
    _inherit = 'website.seo.metadata'

    def _compute_json_ld(self):
        for record in self:
            record.json_ld = None

    @api.depends('json_ld')
    def _compute_pprint_json_ld(self):
        for record in self:
            if record.json_ld:
                record.pprint_json_ld = pprint.pformat(json.loads(record.json_ld))
            else:
                record.pprint_json_ld = None

    def _compute_breadcrumb(self):
        for record in self:
            record.breadcrumb = None

    website_meta_img = fields.Image('Website meta image')
    json_ld = fields.Char('JSON-LD', compute='_compute_json_ld', store=False, readonly=True)
    pprint_json_ld = fields.Text('JSON-LD (Pretty)', compute='_compute_pprint_json_ld', store=False, readonly=True)
    breadcrumb = fields.Char('Breadcrumb', compute='_compute_breadcrumb', store=False, readonly=True)


class Website(models.Model):
    _name = 'website'
    _inherit = ['website', 'website.seo.metadata']

    def _compute_json_ld(self):
        for website in self:
            base_url = website._alokai_domain()

            company = website.company_id

            # Discover all social_* fields on the website model and emit any that
            # have a value. Unwanted channels (facebook, youtube, etc.) get cleared
            # by the post-init hook so they won't appear here.
            social = [
                getattr(website, fname)
                for fname in website._fields
                if fname.startswith('social_') and getattr(website, fname, None)
            ]

            # Build address only with the fields that actually have values
            address_fields = {}
            if company.street:
                address_fields["streetAddress"] = company.street
            if company.street2:
                if address_fields.get('streetAddress'):
                    address_fields['streetAddress'] += ', ' + company.street2
                else:
                    address_fields["streetAddress"] = company.street2
            if company.city:
                address_fields["addressLocality"] = company.city
            if company.state_id:
                address_fields["addressRegion"] = company.state_id.name
            if company.zip:
                address_fields["postalCode"] = company.zip
            if company.country_id:
                address_fields["addressCountry"] = company.country_id.name

            # OnlineStore is a more specific schema.org type for e-commerce sites
            json_ld = {
                "@context": "https://schema.org",
                "@type": "OnlineStore",
                "name": website.name,
                "url": base_url or '',
            }

            if base_url and website.id:
                json_ld["logo"] = f'{base_url}/web/image/website/{website.id}/logo'

            if social:
                json_ld["sameAs"] = social

            if company.phone:
                json_ld["contactPoint"] = {
                    "@type": "ContactPoint",
                    "telephone": company.phone,
                    "contactType": "customer service",
                }

            # Only emit address if at least one field is set
            if address_fields:
                json_ld["address"] = {
                    "@type": "PostalAddress",
                    **address_fields,
                }

            website.json_ld = json.dumps(json_ld)

    @api.model
    def _alokai_resolve_by_host(self, host):
        """Resolve which website a storefront/GraphQL request belongs to by
        matching the request host against each website's Domain.

        Matching ignores scheme, path/trailing slash and case, so e.g.
        'https://shop.example.com/' matches a stored 'shop.example.com'.

        Zero-config friendly: with a single website it always returns it, so
        a fresh install works with no setup. With several websites and no
        match it still returns one (a request is never broken) but logs a
        warning - that's the only case where it could otherwise silently
        serve the wrong store's data.
        """
        # Resolution runs on every GraphQL request, so cache host -> id (see
        # _alokai_website_id_by_host). Website.write()/create() clear the
        # registry caches, so a Domain change invalidates this automatically.
        return self.browse(self._alokai_website_id_by_host(host))

    @tools.ormcache('host')
    def _alokai_website_id_by_host(self, host):
        """Cached host -> website id used by :meth:`_alokai_resolve_by_host`."""
        def _norm(value):
            value = (value or '').strip().lower()
            value = value.split('://', 1)[-1]   # drop scheme
            value = value.split('/', 1)[0]      # drop path / trailing slash
            return value

        websites = self.search([])
        target = _norm(host)
        if target:
            for website in websites:
                if _norm(website._alokai_domain()) == target:
                    return website.id

        # Only warn when a host was provided but matched nothing.
        if target and len(websites) > 1:
            _logger.warning(
                "Alokai: no website Domain matched request host %r; falling "
                "back to %r. Set each website's Domain to its storefront host "
                "to route requests correctly.",
                host, websites[:1].display_name,
            )
        return websites[:1].id

    @api.model
    def _redis_enabled(self):
        """Redis is opt-in and disabled by default. Every Redis code path
        checks this and no-ops when it's off."""
        return self.env['ir.config_parameter'].sudo().get_param('alokai_redis_enabled') == 'True'

    @api.model
    def _alokai_sync_mode(self):
        """Where stock and slugs go: 'redis' when Odoo writes them to Redis
        itself, 'zeppln' when a Zeppln storefront has reported in - its Redis
        is private, so they are sent to the storefront instead - else
        nowhere (False)."""
        if self._redis_enabled():
            return 'redis'
        if self.env['ir.config_parameter'].sudo().get_param('graphql_alokai.saas_instance_created'):
            return 'zeppln'
        return False

    @api.model
    def _alokai_trigger_dirty_cron(self):
        # Run the dirty cron now rather than on its next tick. Called inline
        # (not via postcommit): the ir.cron.trigger row must commit with this
        # transaction - postcommit runs after the final COMMIT, so a row
        # created there is rolled back on cursor close. Redundant triggers in
        # one transaction are fine: the worker consumes all pending triggers
        # in a single run.
        cron = self.env.ref(
            'graphql_alokai.ir_cron_update_dirty_products_stock_redis',
            raise_if_not_found=False,
        )
        if cron:
            cron._trigger()

    @api.model
    def _alokai_mark_slugs_dirty(self, records, previous=frozenset()):
        """Have the dirty cron bring the slug keys of `records` up to date.

        `previous` is the slugs they had before the change, for the cron to
        remove those they no longer answer at. Nothing is written to Redis or
        sent here: a save rolled back after it would leave keys for a state
        that never existed, and an unreachable Redis would lose the change.
        """
        mode = self._alokai_sync_mode()
        if not mode or not records:
            return
        entries = [
            {'model': records._name, 'ids': list(ids), 'previous': sorted(previous)}
            for ids in split_every(DIRTY_BATCH, records.ids)
        ]
        if mode == 'redis':
            # After commit, as the stock flags: a rolled-back save marks none.
            self.env.cr.postcommit.add(lambda: self._write_dirty_slugs_redis(entries))
        else:
            # In the same transaction: a rolled-back save leaves no row.
            self.env['alokai.zeppln.dirty']._add('slug', entries)
        self._alokai_trigger_dirty_cron()

    @api.model
    def _write_dirty_slugs_redis(self, entries):
        redis_client = self._redis_connect()
        if not redis_client:
            return
        try:
            # Not under slug:, which the storefront scans for its pages.
            pipe = redis_client.pipeline()
            for entry in entries:
                pipe.set(f'dirty:slug-{entry["model"]}-{uuid.uuid4().hex}', json.dumps(entry))
            pipe.execute()
        finally:
            redis_client.close()

    @api.model
    def _alokai_slug_changes(self, entries):
        """``{model: (current, gone)}`` for dirty slug entries, from the records
        as they are now."""
        grouped = defaultdict(lambda: (set(), set()))
        for entry in entries:
            ids, previous = grouped[entry['model']]
            ids.update(entry['ids'])
            previous.update(entry['previous'])
        return {
            model: self.env[model].sudo().browse(ids).exists()._slug_changes(previous)
            for model, (ids, previous) in grouped.items()
            if model in self.env
        }

    @api.model
    def _zeppln_website(self):
        website_id = self.env['ir.config_parameter'].sudo().get_param('graphql_alokai.saas_website_id')
        return self.sudo().browse(int(website_id or 0)).exists()

    def _zeppln_post(self, route, payload):
        """One request to the Zeppln storefront, signed with the website token
        as its zepplnSecurity checks it: HMAC-SHA256 of
        METHOD:PATH:TIMESTAMP:BODY."""
        self.ensure_one()
        path = f'/api/internal/zeppln/{route}'
        body = json.dumps(payload, separators=(',', ':'))
        timestamp = str(int(time.time() * 1000))
        signature = hmac.new(
            self.alokai_saas_token.encode(), f'POST:{path}:{timestamp}:{body}'.encode(),
            hashlib.sha256).hexdigest()
        base_url = self.alokai_domain.rstrip('/')
        if '://' not in base_url:
            base_url = 'https://' + base_url
        response = requests.post(base_url + path, data=body.encode(), timeout=30, headers={
            'Content-Type': 'application/json',
            'x-zeppln-timestamp': timestamp,
            'x-zeppln-signature': signature,
        })
        response.raise_for_status()

    @api.model
    def _redis_connect(self):
        # Single gate: when Redis is disabled, return None so callers skip.
        if not self._redis_enabled():
            return None

        ICP = self.env['ir.config_parameter'].sudo()
        redis_host = ICP.get_param('alokai_redis_host', False)
        redis_port = ICP.get_param('alokai_redis_port', False)

        if not redis_host or not redis_port:
            raise UserError(_('Please configure Redis.'))

        try:
            redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                decode_responses=True,
            )
            redis_client.ping()

            return redis_client
        except TimeoutError:
            raise UserError(_('Timeout while connecting to Redis.'))
        except AuthenticationError:
            raise UserError(_('Invalid username or password.'))
        except ConnectionError:
            raise UserError(_('Unable to connect to Redis.'))

    def redis_flushdb(self):
        """
        Delete only keys that have a TTL set. Keys without expiration are
        treated as persistent data (cart, stock, slug entries, etc.) and kept.

        SCAN iterates keys in batches without blocking Redis. TTL is checked
        per-batch via a pipeline so we make one round-trip per batch instead
        of one per key. TTL semantics:
            -2 = key doesn't exist (race with another deletion)
            -1 = key exists but has no expire
            >0 = key has expiration in seconds -> delete
        """
        batch_size = 100
        redis_client = self._redis_connect()
        if not redis_client:
            return

        try:
            cursor = 0
            while True:
                cursor, keys = redis_client.scan(cursor, match='*', count=batch_size)

                if keys:
                    pipe = redis_client.pipeline()
                    for key in keys:
                        pipe.ttl(key)
                    ttls = pipe.execute()

                    keys_to_delete = [key for key, ttl in zip(keys, ttls) if ttl > 0]
                    if keys_to_delete:
                        redis_client.delete(*keys_to_delete)

                if cursor == 0:
                    break
        finally:
            redis_client.close()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Redis flushed'),
                'message': _('Redis cache has been successfully flushed.'),
                'type': 'success',
                'sticky': False,
                'fadeout': 'slow',
            },
        }

    # Rebuilt on the new storefront domain whenever the domain changes; the
    # forms only edit their paths.
    alokai_payment_success_return_url = fields.Char(
        'Payment Success Return Url',
        compute='_compute_alokai_payment_return_urls', store=True, readonly=False)
    alokai_payment_error_return_url = fields.Char(
        'Payment Error Return Url',
        compute='_compute_alokai_payment_return_urls', store=True, readonly=False)
    alokai_storefront_url = fields.Char('Storefront URL', compute='_compute_alokai_storefront_url')
    alokai_payment_success_path = fields.Char(
        'Payment Success Return Path', compute='_compute_alokai_payment_paths',
        inverse='_inverse_alokai_payment_paths',
        help="Storefront page a shopper returns to after paying.")
    alokai_payment_error_path = fields.Char(
        'Payment Error Return Path', compute='_compute_alokai_payment_paths',
        inverse='_inverse_alokai_payment_paths',
        help="Storefront page a shopper returns to when the payment fails.")
    user_id = fields.Many2one(domain=lambda self: [('group_ids', 'in', self.env.ref('base.group_public').id)])
    alokai_public_pricelist_id = fields.Many2one(
        'product.pricelist', 'Public Pricelist',
        compute='_compute_alokai_public_pricelist_id', inverse='_inverse_alokai_public_pricelist_id',
        domain="[('company_id', 'in', (company_id, False))]",
        help="Pricelist of the Public User: the prices anonymous shoppers see.")
    alokai_mailing_list_id = fields.Many2one('mailing.list', 'Newsletter', domain=[('is_public', '=', True)])
    reset_password_email_template_id = fields.Many2one('mail.template', string='Reset Password')
    order_confirmation_email_template_id = fields.Many2one('mail.template', string='Order confirmation')
    alokai_domain = fields.Char(
        'Alokai Domain',
        help="Public storefront host used to route GraphQL requests to this "
             "website and to build storefront links. Falls back to the "
             "website's Domain when empty."
    )

    alokai_saas_token = fields.Char(
        'Alokai SaaS Token', copy=False, groups='base.group_system',
        help="Authenticates the SaaS callback that reports the domain of the "
             "Alokai instance created for this website.")

    @api.depends('alokai_domain', 'domain')
    def _compute_alokai_storefront_url(self):
        for website in self:
            website.alokai_storefront_url = website._alokai_domain()

    # The partner's pricelist is per company: read and write it in the
    # website's company, not the current user's.
    @api.depends('user_id', 'company_id')
    def _compute_alokai_public_pricelist_id(self):
        for website in self:
            partner = website.user_id.partner_id.with_company(website.company_id)
            website.alokai_public_pricelist_id = partner.property_product_pricelist

    def _inverse_alokai_public_pricelist_id(self):
        for website in self:
            partner = website.user_id.partner_id.sudo().with_company(website.company_id)
            partner.property_product_pricelist = website.alokai_public_pricelist_id

    @api.depends('alokai_domain', 'domain')
    def _compute_alokai_payment_return_urls(self):
        # Keeps each URL's path (its current value is still the old one here).
        for website in self:
            for url_field, (_path_field, default_path) in PAYMENT_RETURN_FIELDS.items():
                path = urllib.parse.urlsplit(website[url_field] or '').path
                if not path.startswith('/'):
                    path = default_path
                website[url_field] = website._alokai_domain() + path

    @api.depends('alokai_payment_success_return_url', 'alokai_payment_error_return_url')
    def _compute_alokai_payment_paths(self):
        for website in self:
            for url_field, (path_field, _default_path) in PAYMENT_RETURN_FIELDS.items():
                website[path_field] = urllib.parse.urlsplit(website[url_field] or '').path

    def _inverse_alokai_payment_paths(self):
        for website in self:
            for url_field, (path_field, default_path) in PAYMENT_RETURN_FIELDS.items():
                path = website[path_field] or default_path
                website[url_field] = website._alokai_domain() + '/' + path.lstrip('/')

    def _alokai_domain(self):
        """Storefront base URL: the dedicated Alokai Domain, else the website's
        Domain, else the system base URL. Trailing slash stripped, ready to
        prefix a slug."""
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        return (self.alokai_domain or self.domain or base_url).rstrip('/')

    @api.model
    def enable_b2c_reset_password(self):
        """ Enable sign up and reset password on default website """
        website = self.env.ref('website.default_website', raise_if_not_found=False)
        if website:
            website.auth_signup_uninvited = 'b2c'

        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('auth_signup.invitation_scope', 'b2c')
        ICP.set_param('auth_signup.reset_password', True)

    @api.model
    def _update_all_slugs_redis(self):
        """Mark every slug dirty, for the dirty cron to write. The keys are not
        wiped first: the storefront answers 404 for a URL without one, so
        every page would be gone until the cron got to it."""
        for model in SLUG_MODELS:
            self._alokai_mark_slugs_dirty(self.env[model].sudo().search([]))

    @api.model
    def _alokai_demo_enable_redis(self):
        """Called at the end of the post-init hook when demo data is loaded.
        The storefront resolves URLs
        through the slug keys in Redis, so a demo database without them shows
        empty categories. Enable Redis and push the demo catalogue only when
        a server is reachable, so installing demo data never requires Redis."""
        ICP = self.env['ir.config_parameter'].sudo()
        try:
            redis.Redis(
                host=ICP.get_param('alokai_redis_host', 'localhost'),
                port=ICP.get_param('alokai_redis_port', 6379),
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            ).ping()
        except redis.exceptions.RedisError:
            _logger.info("Alokai demo: Redis not reachable, leaving it disabled.")
            return
        ICP.set_param('alokai_redis_enabled', 'True')
        # Push slugs without clearing existing slug:* keys, which may belong
        # to other databases sharing the same Redis.
        self.env['product.template'].search([])._update_slug_in_redis()
        self.env['product.public.category'].search([])._update_slug_in_redis()
        self.env['alokai.website.page'].search([])._sync_slugs_in_redis()
        self.env['product.product']._update_all_products_stock_redis()


class WebsiteMenu(models.Model):
    _inherit = 'website.menu'

    is_footer = fields.Boolean('Is Footer', default=False)
    menu_image_ids = fields.One2many('website.menu.image', 'menu_id', string='Menu Images')
    is_mega_menu = fields.Boolean(store=True)


class WebsiteMenuImage(models.Model):
    _name = 'website.menu.image'
    _description = 'Website Menu Image'

    def _default_sequence(self):
        menu = self.search([], limit=1, order="sequence DESC")
        return menu.sequence or 0

    menu_id = fields.Many2one('website.menu', 'Website Menu', required=True, ondelete='cascade')
    sequence = fields.Integer(default=_default_sequence)
    image = fields.Image(string='Image', required=True)
    tag = fields.Char('Tag')
    title = fields.Char('Title')
    subtitle = fields.Char('Subtitle')
    text_color = fields.Char('Text Color (Hex)', help='#111000')
    button_text = fields.Char('Button Text')
    button_url = fields.Char('Button URL')


class BlogTag(models.Model):
    _name = 'blog.tag'
    _inherit = ['blog.tag', 'website.slug.redis.mixin']

    @api.depends('name')
    def _compute_website_slug(self):
        langs = self.env['res.lang'].search([])

        for blog_tag in self:
            for lang in langs:
                blog_tag = blog_tag.with_context(lang=lang.code)

                if not blog_tag.id:
                    blog_tag.website_slug = None
                else:
                    slug_name = self.env['ir.http']._slugify(blog_tag.name or '').strip().strip('-')
                    blog_tag.website_slug = f'/{slug_name}-{blog_tag.id}'

    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)


class BlogBlog(models.Model):
    _name = 'blog.blog'
    _inherit = ['blog.blog', 'website.slug.redis.mixin']

    @api.depends('name')
    def _compute_website_slug(self):
        langs = self.env['res.lang'].search([])

        for blog in self:
            for lang in langs:
                blog = blog.with_context(lang=lang.code)

                if not blog.id:
                    blog.website_slug = None
                else:
                    slug_name = self.env['ir.http']._slugify(blog.name or '').strip().strip('-')
                    blog.website_slug = f'/blog/{slug_name}'

    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)


class BlogPost(models.Model):
    _name = 'blog.post'
    _inherit = ['blog.post', 'website.slug.redis.mixin']

    @api.model
    def _graphql_get_search_order(self, sort):
        sorting = ''
        for field, val in sort.items():
            if sorting:
                sorting += ', '
            sorting += '%s %s' % (field, val.value)

        # Add id as last factor, so we can consistently get the same results
        if sorting:
            sorting += ', published_date DESC, id ASC'
        else:
            sorting = 'published_date DESC, id ASC'

        return sorting

    @api.model
    def _graphql_get_search_domain(self, filter, search):
        env = self.env

        # Only get published products
        domain = [
            [('is_published', '=', True)],
        ]

        if search:
            for srch in search.split(" "):
                domain.append([
                    '|', ('name', 'ilike', srch), ('content', 'ilike', srch)])

        if filter.get('tag_id', False):
            domain.append([('tag_ids', 'in', filter['tag_id'])])
        if filter.get('tag_slug', False):
            domain.append([('tag_ids.website_slug', '=', filter['tag_slug'])])

        return Domain.AND(domain)

    @api.depends('name', 'blog_id.website_slug')
    def _compute_website_slug(self):
        langs = self.env['res.lang'].search([])

        for blog_post in self:
            for lang in langs:
                blog_post = blog_post.with_context(lang=lang.code)

                if not blog_post.id or not blog_post.blog_id:
                    blog_post.website_slug = None
                else:
                    slug_name = self.env['ir.http']._slugify(blog_post.name or '').strip().strip('-')
                    # Guard against blog_id.website_slug being False/None - otherwise
                    # the f-string injects literal "False" into the URL.
                    blog_slug = blog_post.blog_id.website_slug or ''
                    blog_post.website_slug = f'{blog_slug}/{slug_name}-{blog_post.id}'

    def _compute_json_ld(self):
        website = self.env['website'].get_current_website()
        base_url = website._alokai_domain()

        def strip_html(text):
            """Strip HTML tags and decode entities for plain-text description."""
            if not text:
                return ''
            return unescape(re.sub(r'<[^>]+>', '', text)).strip()

        publisher = {
            "@type": "Organization",
            "name": (website and website.display_name) or '',
        }
        # Google requires publisher.logo for Article rich results
        if website and base_url:
            publisher["logo"] = {
                "@type": "ImageObject",
                "url": f"{base_url}/web/image/website/{website.id}/logo",
            }

        for blog in self:
            # mainEntityOfPage.@id must be the article's URL, not the home page.
            # Guard against website_slug being False/None which would inject "False" into URL.
            slug = blog.website_slug or ''
            article_url = f"{base_url}{slug}" if slug else base_url

            # Clean author name: res.partner.display_name is often "Company, Person"
            # (e.g. "YourCompany, Mitchell Admin"). Strip the company prefix for
            # a clean Person entity in the JSON-LD.
            author_name = (blog.author_name or '').strip()
            if ',' in author_name:
                author_name = author_name.split(',', 1)[1].strip()

            # Image must be an absolute URL for Google
            image_url = get_image_url(blog) or ''
            if image_url and not image_url.startswith(('http://', 'https://')):
                image_url = f"{base_url}{image_url}"

            json_ld = {
                "@context": "https://schema.org",
                "@type": "Article",
                "mainEntityOfPage": {
                    "@type": "WebPage",
                    "@id": article_url,
                },
                "headline": (blog.name or '')[:110],  # Google recommends <= 110 chars
                "description": strip_html(blog.teaser_manual or blog.teaser),
                "author": {
                    "@type": "Person",
                    "name": author_name,
                },
                "publisher": publisher,
                "image": image_url,
            }

            if blog.published_date:
                json_ld["datePublished"] = blog.published_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            if blog.post_date:
                json_ld["dateModified"] = blog.post_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')

            blog.json_ld = json.dumps(json_ld)

    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)
