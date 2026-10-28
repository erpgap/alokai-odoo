# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import secrets
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

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
    alokai_saas_instance_created = fields.Boolean(
        'Alokai instance created',
        config_parameter='graphql_alokai.saas_instance_created',
        default=False,
        readonly=True,
        help='Set when the SaaS reports back that the Alokai instance exists.',
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

    def _alokai_public_base_url(self):
        instance_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        # Odoo may be configured with an internal HTTP base URL even though its
        # public reverse-proxy endpoint is HTTPS. Zeppln only accepts HTTPS.
        parsed_url = urlsplit(instance_url)
        if parsed_url.hostname:
            instance_url = urlunsplit(('https', parsed_url.netloc, parsed_url.path, '', '')).rstrip('/')
        return instance_url

    def action_create_alokai_instance(self):
        """Open the local controller, which POSTs the website token to Zeppln."""
        self.ensure_one()
        website = self.website_id.sudo()
        if not website.alokai_saas_token:
            website.alokai_saas_token = secrets.token_urlsafe(32)
        return {
            'type': 'ir.actions.act_url',
            'url': '/zeppln/launch?%s' % urlencode({
                'website_id': website.id,
                'odoo_url': website.domain or self._alokai_public_base_url(),
                'zeppln_url': self._alokai_saas_base_url(),
            }),
            'target': 'new',
        }

    def action_open_alokai_instance(self):
        """Open the storefront of the Alokai instance reported by the SaaS."""
        website_id = self.env['ir.config_parameter'].sudo().get_param('graphql_alokai.saas_website_id')
        website = self.env['website'].sudo().browse(int(website_id or 0)).exists()
        if not website.alokai_domain:
            raise UserError(_('The Alokai instance has not reported its domain yet.'))
        url = website.alokai_domain
        if '://' not in url:
            url = 'https://%s' % url
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }
