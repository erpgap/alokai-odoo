# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Login with two-factor authentication, and resetPassword's output."""

import base64
import os
import time

from odoo.tests import tagged
from odoo.tests.common import new_test_user
from odoo.addons.auth_totp.models.totp import hotp, TIMESTEP

from .common import AlokaiGraphQLCommon

LOGIN = """
    mutation ($email: String!, $password: String!) {
      login(email: $email, password: $password, subscribeNewsletter: false) {
        user { id name email totpRequired partner { id phone street } }
        cart { id }
        wishlistItems { id }
      }
    }
"""
TOTP = """
    mutation ($code: String!, $uid: Int!) {
      totpVerification(code: $code, userId: $uid, rememberDevice: false) {
        user { id }
      }
    }
"""
# Only a logged-in customer may update their account.
UPDATE_ACCOUNT = """
    mutation { updateMyAccount(myaccount: {phone: "555-0000"}) { id } }
"""


def _enable_totp(user):
    key = os.urandom(20)
    user.sudo().totp_secret = base64.b32encode(key).decode()
    return key


def _code(key):
    return str(hotp(key, int(time.time() / TIMESTEP))).zfill(6)


@tagged('post_install', '-at_install', 'alokai')
class TestLogin2FA(AlokaiGraphQLCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_key = _enable_totp(cls.user)
        cls.other_user = new_test_user(
            cls.env, login='alokai_other_user', password='other-pw-123',
            groups='base.group_portal', email='alokai_other_user@example.com')
        cls.other_key = _enable_totp(cls.other_user)

    def _login_pending_2fa(self):
        body = self._gql(LOGIN, {'email': self.user.login, 'password': self.password})
        return body['data']['login']

    def test_login_pending_2fa_returns_only_the_user(self):
        result = self._login_pending_2fa()
        self.assertEqual(result['user']['id'], self.user.id)
        self.assertTrue(result['user']['totpRequired'])
        self.assertIsNone(result['user']['partner'])
        self.assertEqual(result['user']['name'], self.user.login)
        self.assertIsNone(result['cart'])
        self.assertEqual(result['wishlistItems'], [])
        self._gql(UPDATE_ACCOUNT, expect_errors=True)

    def test_code_is_verified_for_the_pending_user(self):
        self._login_pending_2fa()
        code = _code(self.other_key)
        self._gql(TOTP, {'code': code, 'uid': self.other_user.id}, expect_errors=True)
        self._gql(TOTP, {'code': code, 'uid': self.user.id}, expect_errors=True)
        self._gql(UPDATE_ACCOUNT, expect_errors=True)

    def test_code_without_pending_login(self):
        self._gql(TOTP, {'code': _code(self.other_key), 'uid': self.other_user.id},
                  expect_errors=True)
        self._gql(UPDATE_ACCOUNT, expect_errors=True)

    def test_login_with_2fa(self):
        self._login_pending_2fa()
        body = self._gql(TOTP, {'code': _code(self.user_key), 'uid': self.user.id})
        self.assertEqual(body['data']['totpVerification']['user']['id'], self.user.id)
        body = self._gql(UPDATE_ACCOUNT)
        self.assertEqual(body['data']['updateMyAccount']['id'], self.partner.id)

    def test_reset_password_returns_only_the_user(self):
        body = self._gql("""
            mutation ($email: String!) {
              resetPassword(email: $email) { id name email partner { id phone street } }
            }
        """, {'email': self.other_user.login})
        result = body['data']['resetPassword']
        self.assertEqual(result['id'], self.other_user.id)
        self.assertEqual(result['name'], self.other_user.login)
        self.assertIsNone(result['partner'])
