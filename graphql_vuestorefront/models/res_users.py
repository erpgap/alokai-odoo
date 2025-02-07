# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import logging

from odoo.http import request
from odoo import api, models, fields, _
from odoo.addons.auth_signup.models.res_partner import now
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _get_website_reset_password_email_template(self):
        website = self.env['website'].get_current_website()
        return website.reset_password_email_template_id

    def api_action_reset_password(self):
        """ create signup token for each user, and send their signup url by email """
        if self.filtered(lambda user: not user.active):
            raise UserError(_("You cannot perform this action on an archived user."))
        # prepare reset password signup
        create_mode = bool(self.env.context.get('create_user'))

        # no time limit for initial invitation, only for reset password
        expiration = False if create_mode else now(days=+1)

        self.mapped('partner_id').signup_prepare(signup_type="reset", expiration=expiration)

        # send email to users with their signup url
        template = self._get_website_reset_password_email_template()

        assert template._name == 'mail.template'

        website = request.env['website'].get_current_website()
        domain = website.domain or ''
        if domain and domain[-1] == '/':
            domain = domain[:-1]

        email_values = {
            'email_cc': False,
            'auto_delete': True,
            'recipient_ids': [],
            'partner_ids': [],
            'scheduled_date': False,
        }

        for user in self:
            token = user.signup_token
            signup_url = "%s/forgot-password/new-password?token=%s" % (domain, token)
            if not user.email:
                raise UserError(_("Cannot send email: user %s has no email address.", user.name))
            email_values['email_to'] = user.email
            with self.env.cr.savepoint():
                force_send = not create_mode
                template.with_context(lang=user.lang, signup_url=signup_url).send_mail(
                    user.id, force_send=force_send, raise_exception=True, email_values=email_values)
            _logger.info("Password reset email sent for user <%s> to <%s>", user.login, user.email)

    website_cart = fields.Many2one('sale.order', 'Cart', compute='_compute_website_cart', readonly=True)
    website_wishlist = fields.Many2many('product.wishlist', 'Wishlist', compute='_compute_website_wishlist',
                                        readonly=True)

    def _compute_website_cart(self):
        website = self.env['website'].get_current_website()
        for user in self:
            user.cart = website.sale_get_order(force_create=True)

    def _compute_website_wishlist(self):
        for user in self:
            user.wishlist = [(6, 0, self.env['product.wishlist'].current().ids)]
