# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import uuid
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    alokai_debug_mode = fields.Boolean('Debug Mode')
    alokai_payment_success_return_url = fields.Char(
        'Payment Success Return Url', related='website_id.alokai_payment_success_return_url', readonly=False,
        required=True
    )
    alokai_payment_error_return_url = fields.Char(
        'Payment Error Return Url', related='website_id.alokai_payment_error_return_url', readonly=False,
        required=True
    )
    alokai_cache_invalidation = fields.Boolean('Cache Invalidation')
    alokai_cache_invalidation_key = fields.Char('Cache Invalidation Key', required=True)
    alokai_cache_invalidation_url = fields.Char('Cache Invalidation Url', required=True)
    alokai_mailing_list_id = fields.Many2one('mailing.list', 'Newsletter', domain=[('is_public', '=', True)],
                                          related='website_id.alokai_mailing_list_id', readonly=False, required=True)
    reset_password_email_template_id = fields.Many2one('mail.template', string='Reset Password',
                                                       related='website_id.reset_password_email_template_id', readonly=False, required=True)
    order_confirmation_email_template_id = fields.Many2one('mail.template', string='Order confirmation',
                                                           related='website_id.order_confirmation_email_template_id', readonly=False, required=True)

    # Alokai Images
    alokai_image_quality = fields.Integer('Quality (%)', required=True)
    alokai_image_background_rgba = fields.Char('Background RGBA', required=True)
    alokai_image_resize_limit = fields.Integer('Resize Limit', required=True)
    alokai_recent_sales_count_days = fields.Integer('Recent Sales Count (days)', required=True)

    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        res.update(
            alokai_debug_mode=ICP.get_param('alokai_debug_mode'),
            alokai_cache_invalidation=ICP.get_param('alokai_cache_invalidation'),
            alokai_cache_invalidation_key=ICP.get_param('alokai_cache_invalidation_key'),
            alokai_cache_invalidation_url=ICP.get_param('alokai_cache_invalidation_url'),
            alokai_image_quality=int(ICP.get_param('alokai_image_quality', 100)),
            alokai_image_background_rgba=ICP.get_param('alokai_image_background_rgba', '(255, 255, 255, 255)'),
            alokai_image_resize_limit=int(ICP.get_param('alokai_image_resize_limit', 1920)),
            alokai_recent_sales_count_days=int(ICP.get_param('alokai_recent_sales_count_days', 30)),
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
        ICP.set_param('alokai_cache_invalidation', self.alokai_cache_invalidation)
        ICP.set_param('alokai_cache_invalidation_key', self.alokai_cache_invalidation_key)
        ICP.set_param('alokai_cache_invalidation_url', self.alokai_cache_invalidation_url)
        ICP.set_param('alokai_image_quality', self.alokai_image_quality)
        ICP.set_param('alokai_image_background_rgba', self.alokai_image_background_rgba)
        ICP.set_param('alokai_image_resize_limit', self.alokai_image_resize_limit)
        ICP.set_param('alokai_recent_sales_count_days', self.alokai_recent_sales_count_days)

    @api.model
    def create_alokai_cache_invalidation_key(self):
        ICP = self.env['ir.config_parameter'].sudo()
        ICP.set_param('alokai_cache_invalidation_key', str(uuid.uuid4()))
