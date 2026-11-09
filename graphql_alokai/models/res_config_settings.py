# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import secrets
import uuid
from urllib.parse import urlsplit, urlunsplit

import requests

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    alokai_debug_mode = fields.Boolean('Debug Mode')
    alokai_saas_base_url = fields.Char(
        'Zeppln portal URL',
        config_parameter='graphql_alokai.saas_base_url',
        default='https://www.zeppln.com',
        help='Public Zeppln portal used to launch and manage this Alokai instance.',
    )
    alokai_storefront_url = fields.Char(related='website_id.alokai_storefront_url')
    alokai_payment_success_path = fields.Char(
        related='website_id.alokai_payment_success_path', readonly=False, required=True)
    alokai_payment_error_path = fields.Char(
        related='website_id.alokai_payment_error_path', readonly=False, required=True)
    alokai_cache_invalidation_key = fields.Char(
        'Cache Invalidation Key', required=True,
        help="Signs the requests that clear pages from the Alokai cache. "
             "Must match NUXT_CACHE_INVALIDATION_KEY on Alokai.")
    alokai_cache_invalidation_signing_secret = fields.Char(
        'Cache Invalidation Signing Secret',
        help="Only if Alokai sets NUXT_CACHE_INVALIDATION_SIGNING_SECRET: "
             "the same value. Otherwise requests are signed with the key, as "
             "Alokai then expects.")
    alokai_mailing_list_id = fields.Many2one('mailing.list', 'Newsletter', domain=[('is_public', '=', True)],
                                          related='website_id.alokai_mailing_list_id', readonly=False)
    reset_password_email_template_id = fields.Many2one('mail.template', string='Reset Password',
                                                       related='website_id.reset_password_email_template_id', readonly=False, required=True)
    order_confirmation_email_template_id = fields.Many2one('mail.template', string='Order confirmation',
                                                           related='website_id.order_confirmation_email_template_id', readonly=False, required=True)

    # Alokai Images
    alokai_image_quality = fields.Integer('Quality (%)', required=True)
    alokai_image_background_rgba = fields.Char('Background RGBA', required=True)
    alokai_image_resize_limit = fields.Integer('Resize Limit', required=True)
    alokai_recent_sales_count_days = fields.Integer('Recent Sales Count (days)', required=True)

    # Redis
    alokai_redis_enabled = fields.Boolean('Enable Redis')
    alokai_redis_host = fields.Char('Redis Host', default='localhost')
    alokai_redis_port = fields.Integer('Redis Port', default=6379)

    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        res.update(
            alokai_debug_mode=ICP.get_param('alokai_debug_mode'),
            alokai_cache_invalidation_key=ICP.get_param('alokai_cache_invalidation_key'),
            alokai_cache_invalidation_signing_secret=ICP.get_param('alokai_cache_invalidation_signing_secret'),
            alokai_image_quality=int(ICP.get_param('alokai_image_quality', 80)),
            alokai_image_background_rgba=ICP.get_param('alokai_image_background_rgba', '(255, 255, 255, 255)'),
            alokai_image_resize_limit=int(ICP.get_param('alokai_image_resize_limit', 1920)),
            alokai_recent_sales_count_days=int(ICP.get_param('alokai_recent_sales_count_days', 30)),
            alokai_redis_enabled=ICP.get_param('alokai_redis_enabled'),
            alokai_redis_host=ICP.get_param('alokai_redis_host', 'localhost'),
            alokai_redis_port=int(ICP.get_param('alokai_redis_port', 6379)),
        )
        return res

    def set_values(self):
        if self.alokai_image_quality < 0 or self.alokai_image_quality > 100:
            raise ValidationError(_('Invalid image quality percentage.'))

        if self.alokai_image_resize_limit < 0:
            raise ValidationError(_('Invalid image resize limit.'))

        super(ResConfigSettings, self).set_values()
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('alokai_debug_mode', self.alokai_debug_mode)
        ICP.set_param('alokai_cache_invalidation_key', self.alokai_cache_invalidation_key)
        ICP.set_param('alokai_cache_invalidation_signing_secret', self.alokai_cache_invalidation_signing_secret or False)
        ICP.set_param('alokai_image_quality', self.alokai_image_quality)
        ICP.set_param('alokai_image_background_rgba', self.alokai_image_background_rgba)
        ICP.set_param('alokai_image_resize_limit', self.alokai_image_resize_limit)
        ICP.set_param('alokai_recent_sales_count_days', self.alokai_recent_sales_count_days)
        ICP.set_param('alokai_redis_enabled', self.alokai_redis_enabled)
        ICP.set_param('alokai_redis_host', self.alokai_redis_host)
        ICP.set_param('alokai_redis_port', self.alokai_redis_port)

    @api.model
    def create_alokai_cache_invalidation_key(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('alokai_cache_invalidation_key', str(uuid.uuid4()))

    def _alokai_saas_base_url(self):
        ICP = self.env['ir.config_parameter'].sudo()
        saas_base_url = (ICP.get_param(
            'graphql_alokai.saas_base_url',
            'https://www.zeppln.com',
        ) or 'https://www.zeppln.com').rstrip('/')
        # Transparently migrate the former portal URL for existing databases.
        if saas_base_url == 'https://zeppln.erpgap.com':
            saas_base_url = 'https://www.zeppln.com'
            ICP.set_param('graphql_alokai.saas_base_url', saas_base_url)
        return saas_base_url

    def _alokai_public_base_url(self, website=None):
        """Public Odoo URL sent to Zeppln for launch validation.

        Prefer the selected website domain (multi-website / vanity domains)
        and fall back to global web.base.url when absent.
        """
        instance_url = ''
        if website:
            instance_url = (website.domain or '').strip().rstrip('/')
            if instance_url and '://' not in instance_url:
                instance_url = f'https://{instance_url}'
        if not instance_url:
            instance_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        # Odoo may be configured with an internal HTTP base URL even though its
        # public reverse-proxy endpoint is HTTPS. Zeppln only accepts HTTPS.
        parsed_url = urlsplit(instance_url)
        if parsed_url.hostname:
            instance_url = urlunsplit(('https', parsed_url.netloc, parsed_url.path, '', '')).rstrip('/')
        return instance_url

    def _alokai_new_website_name(self):
        names = set(self.env['website'].sudo().search([]).mapped('name'))
        name = candidate = _('Zeppln')
        number = 1
        while candidate in names:
            number += 1
            candidate = '%s %s' % (name, number)
        return candidate

    def action_create_alokai_instance(self):
        """Create a Zeppln instance. Each gets a website of its own - one
        storefront per website - with a token, which Odoo hands to Zeppln
        server to server. Zeppln answers with the page to carry on in, opened
        for the user. Once the instance is up, Zeppln reports its URL on
        /zeppln/instance-ready with that token (controllers/zeppln.py).

        A website whose instance has not reported its URL yet is launched
        again rather than joined by a new one: otherwise every click left a
        website no instance would ever answer for."""
        self.ensure_one()
        Website = self.env['website'].sudo()
        # Prefer the website selected in settings so launch URL/token match it.
        website = self.website_id.sudo() if self.website_id else Website.browse()
        if not website:
            website = Website.search([
                ('alokai_saas_token', '!=', False), ('alokai_domain', '=', False),
            ], order='id', limit=1)
        if not website:
            website = Website.create({
                'name': self._alokai_new_website_name(),
                'alokai_saas_token': secrets.token_urlsafe(32),
            })
        elif not website.alokai_saas_token:
            website.write({'alokai_saas_token': secrets.token_urlsafe(32)})
        # The admin's setting, never the request: the token goes there.
        base_url = self._alokai_saas_base_url()
        try:
            response = requests.post(base_url + '/instance/launch', data={
                'instance': self._alokai_public_base_url(website),
                'odooToken': website.alokai_saas_token,
            }, allow_redirects=False, timeout=20)
        except requests.RequestException:
            # Raising rolls the new website back with the rest.
            raise UserError(_('Zeppln could not be reached. Please try again later.'))
        location = response.headers.get('Location')
        if response.status_code not in (301, 302, 303, 307, 308) or not location:
            raise UserError(_('Zeppln did not accept the request (HTTP %s).', response.status_code))
        # Only Zeppln's path and query: a misconfigured or compromised answer
        # cannot send the user to another host.
        parsed = urlsplit(location)
        url = base_url + (parsed.path or '/signup')
        if parsed.query:
            url += '?' + parsed.query
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }
