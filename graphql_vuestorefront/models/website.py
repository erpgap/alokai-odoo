# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import pprint
import json
import requests
from odoo import models, fields, api
from odoo.exceptions import ValidationError
from odoo import _
from odoo.addons.http_routing.models.ir_http import slugify
from odoo.addons.graphql_vuestorefront.schemas.objects import get_image_url


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

    website_meta_img = fields.Image('Website meta image')
    json_ld = fields.Char('JSON-LD', compute='_compute_json_ld', store=False, readonly=True)
    pprint_json_ld = fields.Text('JSON-LD', compute='_compute_pprint_json_ld', store=False, readonly=True)


class Website(models.Model):
    _name = 'website'
    _inherit = ['website', 'website.seo.metadata']

    def _compute_json_ld(self):
        for website in self:
            base_url = website.domain or ''
            if base_url and base_url[-1] == '/':
                base_url = base_url[:-1]

            company = website.company_id

            social_fields = [
                'social_twiter',
                'social_facebook',
                'social_github',
                'social_linkedin',
                'social_youtube',
                'social_instagram',
                'social_tiktok',
            ]

            social = list()
            for social_field in social_fields:
                value = getattr(website, social_field, None)
                if value:
                    social.append(value)

            address = {
                "@type": "PostalAddress",
            }
            if company.street:
                address.update({"streetAddress": company.street})
            if company.street2:
                if address.get('streetAddress'):
                    address['streetAddress'] += ', ' + company.street2
                else:
                    address.update({"streetAddress": company.street2})
            if company.city:
                address.update({"addressLocality": company.city})
            if company.state_id:
                address.update({"addressRegion": company.state_id.name})
            if company.zip:
                address.update({"postalCode": company.zip})
            if company.country_id:
                address.update({"addressCountry": company.country_id.name})

            json_ld = {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": website.name,
            "url": website.domain or '',
            "logo": f'{base_url}/web/image/website/{website.id}/logo',
            }

            if social:
                json_ld.update({
                    "sameAs": social,
                })

            if company.phone or company.mobile:
                json_ld.update({
                    "contactPoint": {
                        "@type": "ContactPoint",
                        "telephone": company.phone or company.mobile,
                    }
                })

            json_ld.update({
                "address": address
            })

            website.json_ld = json.dumps(json_ld)

    vsf_payment_success_return_url = fields.Char(
        'Payment Success Return Url', required=True, translate=True, default='Dummy'
    )
    vsf_payment_error_return_url = fields.Char(
        'Payment Error Return Url', required=True,  translate=True, default='Dummy'
    )
    vsf_mailing_list_id = fields.Many2one('mailing.list', 'Newsletter', domain=[('is_public', '=', True)])
    reset_password_email_template_id = fields.Many2one('mail.template', string='Reset Password')
    order_confirmation_email_template_id = fields.Many2one('mail.template', string='Order confirmation')

    @api.model
    def enable_b2c_reset_password(self):
        """ Enable sign up and reset password on default website """
        website = self.env.ref('website.default_website', raise_if_not_found=False)
        if website:
            website.auth_signup_uninvited = 'b2c'

        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('auth_signup.invitation_scope', 'b2c')
        ICP.set_param('auth_signup.reset_password', True)


class WebsiteRewrite(models.Model):
    _inherit = 'website.rewrite'

    def _get_vsf_tags(self):
        tags = 'WR%s' % self.id
        return tags

    def _vsf_request_cache_invalidation(self):
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('vsf_cache_invalidation_url', False)
        key = ICP.get_param('vsf_cache_invalidation_key', False)

        if url and key:
            try:
                for website_rewrite in self:
                    tags = website_rewrite._get_vsf_tags()

                    # Make the GET request to the /cache-invalidate
                    requests.get(url, params={'key': key, 'tags': tags}, timeout=5)
            except:
                pass

    def write(self, vals):
        res = super(WebsiteRewrite, self).write(vals)
        self._vsf_request_cache_invalidation()
        return res

    def unlink(self):
        self._vsf_request_cache_invalidation()
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


class BlogBlog(models.Model):
    _inherit = 'blog.blog'

    def _validate_website_slug(self):
        for blog in self.filtered(lambda c: c.website_slug):
            if blog.website_slug[0] != '/':
                raise ValidationError(_('Slug should start with /'))

            if self.search([('website_slug', '=', blog.website_slug), ('id', '!=', blog.id)], limit=1):
                raise ValidationError(_('Slug is already in use: {}'.format(blog.website_slug)))

    website_slug = fields.Char('Website Slug', translate=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        res = super(BlogBlog, self).create(vals_list)

        for rec in res:
            if rec.website_slug:
                rec._validate_website_slug()
            else:
                rec.website_slug = f'/blog/{rec.id}'

        return res

    def write(self, vals):
        res = super(BlogBlog, self).write(vals)
        if vals.get('website_slug', False):
            self._validate_website_slug()
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return res


class BlogPost(models.Model):
    _inherit = 'blog.post'

    @api.depends('name')
    def _compute_website_slug(self):
        langs = self.env['res.lang'].search([])

        for blog_post in self:
            for lang in langs:
                blog_post = blog_post.with_context(lang=lang.code)

                if not blog_post.id or not blog_post.blog_id:
                    blog_post.website_slug = None
                else:
                    slug_name = slugify(blog_post.name or '').strip().strip('-')
                    blog_post.website_slug = f'{blog_post.blog_id.website_slug}/{slug_name}-{blog_post.id}'

    def _compute_json_ld(self):
        website = self.env['website'].get_current_website()
        base_url = website.domain or ''
        if base_url and base_url[-1] == '/':
            base_url = base_url[:-1]

        for blog in self:
            json_ld = {
                "@context": "https://schema.org",
                "@type": "Article",
                "mainEntityOfPage": {
                    "@type": "WebPage",
                    "@id": base_url
                },
                "headline": blog.name,
                "description": blog.teaser_manual or blog.teaser,
                "author": {
                    "@type": "Person",
                    "name": blog.author_name,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": website and website.display_name
                },
                "datePublished": blog.published_date.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                "dateModified": blog.post_date.strftime('%Y-%m-%dT%H:%M:%S+00:00'),
                "image": get_image_url(blog)
            }

            blog.json_ld = json.dumps(json_ld)

    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)
