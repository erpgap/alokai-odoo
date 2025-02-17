# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

from odoo import api, _
from odoo.exceptions import ValidationError


def pre_init_hook_login_check(env):
    """
    This hook will see if exists any conflict between Portal logins, before the module is installed
    """
    check_users = []
    users = env['res.users'].search([])
    for user in users:
        if user.login and user.has_group('base.group_portal'):
            login = user.login.lower()
            if login not in check_users:
                check_users.append(login)
            else:
                raise ValidationError(
                    _("Conflicting user logins exist for `%s`", login)
                )


def post_init_hook_login_convert(env):
    """
    After the module is installed, set Portal Logins to lowercase
    """
    users = env['res.users'].search([])
    for user in users:
        if user.login and user.has_group('base.group_portal'):
            user.login = user.login.lower()
