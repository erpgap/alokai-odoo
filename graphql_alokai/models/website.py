# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import re
import logging
import redis
import pprint
import json
import requests
import urllib.parse
from html import unescape
from odoo import models, fields, api, tools
from odoo import _
from odoo.addons.graphql_alokai.schemas.objects import get_image_url
from odoo.fields import Domain
from odoo.exceptions import UserError
from redis.exceptions import TimeoutError, AuthenticationError, ConnectionError

_logger = logging.getLogger(__name__)


class WebsiteSlugRedisMixin(models.AbstractModel):
    _name = 'website.slug.redis.mixin'
    _description = 'Mixin to sync website slugs with Redis'

    def _update_slug_in_redis(self):
        redis_client = self.env['website']._redis_connect()
        if not redis_client:
            return
        try:
            langs = self.env['res.lang'].search([])
            pipe = redis_client.pipeline()

            for record in self:
                # Optional: skip unpublished or not relevant records
                if hasattr(record, 'is_published') and not record.is_published:
                    continue
                if hasattr(record, 'sale_ok') and not record.sale_ok:
                    continue

                for lang in langs:
                    slug = record.with_context(lang=lang.code).website_slug
                    if slug:
                        encoded_slug = urllib.parse.quote(slug, safe='')
                        pipe.set(f'slug:{encoded_slug}', record._name)

            pipe.execute()
        finally:
            redis_client.close()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._update_slug_in_redis()
        return records

    def write(self, vals):
        res = super().write(vals)
        self._update_slug_in_redis()
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
                    return website

        if len(websites) > 1:
            _logger.warning(
                "Alokai: no website Domain matched request host %r; falling "
                "back to %r. Set each website's Domain to its storefront host "
                "to route requests correctly.",
                host, websites[:1].display_name,
            )
        return websites[:1]

    @api.model
    def _redis_enabled(self):
        """Redis is opt-in and disabled by default. Every Redis code path
        checks this and no-ops when it's off."""
        return self.env['ir.config_parameter'].sudo().get_param('alokai_redis_enabled') == 'True'

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

    alokai_payment_success_return_url = fields.Char(
        'Payment Success Return Url', required=True, translate=True, default='Dummy'
    )
    alokai_payment_error_return_url = fields.Char(
        'Payment Error Return Url', required=True,  translate=True, default='Dummy'
    )
    alokai_mailing_list_id = fields.Many2one('mailing.list', 'Newsletter', domain=[('is_public', '=', True)])
    reset_password_email_template_id = fields.Many2one('mail.template', string='Reset Password')
    order_confirmation_email_template_id = fields.Many2one('mail.template', string='Order confirmation')
    alokai_domain = fields.Char(
        'Alokai Domain',
        help="Public storefront host used to route GraphQL requests to this "
             "website and to build storefront links. Falls back to the "
             "website's Domain when empty."
    )

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
        redis_client = self.env['website']._redis_connect()
        if not redis_client:
            return
        try:
            # Delet one-by-one to avoid Redis blocking or memory pressure
            delete_keys = list(redis_client.scan_iter('slug:*'))
            for delete_key in delete_keys:
                redis_client.delete(delete_key)
        finally:
            redis_client.close()

        self.env['product.template'].search([])._update_slug_in_redis()
        self.env['product.public.category'].search([])._update_slug_in_redis()
        self.env['blog.tag'].search([])._update_slug_in_redis()
        self.env['blog.blog'].search([])._update_slug_in_redis()
        self.env['blog.post'].search([])._update_slug_in_redis()


class WebsiteRewrite(models.Model):
    _inherit = 'website.rewrite'

    def _get_alokai_tags(self):
        tags = 'WR%s' % self.id
        return tags

    def _alokai_request_cache_invalidation(self):
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('alokai_cache_invalidation_url', False)
        key = ICP.get_param('alokai_cache_invalidation_key', False)

        if url and key:
            try:
                for website_rewrite in self:
                    tags = website_rewrite._get_alokai_tags()

                    # Make the GET request to the /cache-invalidate
                    requests.get(url, params={'key': key, 'tags': tags}, timeout=5)
            except:
                pass

    def write(self, vals):
        res = super(WebsiteRewrite, self).write(vals)
        self._alokai_request_cache_invalidation()
        return res

    def unlink(self):
        self._alokai_request_cache_invalidation()
        return super(WebsiteRewrite, self).unlink()


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

    def write(self, vals):
        res = super(BlogBlog, self).write(vals)
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return res


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
