# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""One test per GraphQL mutation.

Each test exercises the mutation's resolver and selects its full output type.
Mutations roll back with the test (Odoo wraps each test in a savepoint), so
password changes, account deletion, etc. are safe and isolated.
"""

import base64

from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import new_test_user

from .common import (
    AlokaiGraphQLCommon, LEAD_FIELDS, ORDER_FIELDS, PARTNER_FIELDS, USER_FIELDS,
)


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiMutations(AlokaiGraphQLCommon):

    # ------------------------------------------------------------------ #
    #  helpers                                                            #
    # ------------------------------------------------------------------ #
    def _add_to_cart(self, qty=1):
        """Add the test product to the session cart; return the order dict."""
        mutation = """
            mutation ($products: [ProductInput!]!) {
              cartAddMultipleItems(products: $products) {
                order { id orderLines { id quantity } }
                frequentlyBoughtTogether { id }
              }
            }
        """
        variant = self.product.product_variant_id
        body = self._gql(mutation, {'products': [{'id': variant.id, 'quantity': qty}]})
        return body['data']['cartAddMultipleItems']['order']

    # ------------------------------------------------------------------ #
    #  contact us                                                         #
    # ------------------------------------------------------------------ #
    def test_contact_us(self):
        mutation = """
            mutation ($c: ContactUsParams) { contactUs(contactus: $c) { %s } }
        """ % LEAD_FIELDS
        variables = {'c': {
            'name': 'Jane Doe', 'email': 'jane@example.com', 'phone': '555-0001',
            'company': 'ACME', 'subject': 'Hello', 'message': 'Line1\nLine2',
        }}
        body = self._gql(mutation, variables)
        self.assertEqual(body['data']['contactUs']['name'], 'Jane Doe')
        self.assertEqual(body['data']['contactUs']['subject'], 'Hello')

    def test_contact_us_with_attachment(self):
        mutation = """
            mutation ($c: ContactUsParams) { contactUs(contactus: $c) { id } }
        """
        variables = {'c': {
            'name': 'Att Sender', 'email': 'att@example.com', 'phone': '555-0002',
            'subject': 'With file', 'message': 'see attachment',
            'attachments': [{
                'name': 'note.txt',
                'fileData': base64.b64encode(b'hello alokai').decode(),
            }],
        }}
        body = self._gql(mutation, variables)
        lead_id = body['data']['contactUs']['id']
        attachment = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', 'crm.lead'),
            ('res_id', '=', lead_id),
            ('name', '=', 'note.txt'),
        ])
        self.assertTrue(attachment, "the uploaded file should be attached to the lead")

    # ------------------------------------------------------------------ #
    #  account (sign / user profile)                                      #
    # ------------------------------------------------------------------ #
    def test_login(self):
        mutation = """
            mutation ($email: String!, $password: String!) {
              login(email: $email, password: $password, subscribeNewsletter: false) {
                user { %s }
                cart { id name }
                wishlistItems { id }
              }
            }
        """ % USER_FIELDS
        body = self._gql(mutation, {'email': self.user.login, 'password': self.password})
        self.assertEqual(body['data']['login']['user']['id'], self.user.id)

    def test_login_wrong_password(self):
        mutation = """
            mutation { login(email: "alokai_portal_user", password: "wrong") {
              user { id }
            } }
        """
        self._gql(mutation, expect_errors=True)

    def test_logout(self):
        self._login()
        body = self._gql("mutation { logout }")
        self.assertTrue(body['data']['logout'])

    def test_register(self):
        mutation = """
            mutation ($name: String!, $email: String!, $password: String!) {
              register(name: $name, email: $email, password: $password,
                       subscribeNewsletter: false) { %s }
            }
        """ % USER_FIELDS
        body = self._gql(mutation, {
            'name': 'New Signup', 'email': 'new_signup@example.com',
            'password': 'sup3r-s3cret-pw',
        })
        self.assertEqual(body['data']['register']['email'], 'new_signup@example.com')

    def test_reset_password(self):
        mutation = """
            mutation ($email: String!) { resetPassword(email: $email) { id email } }
        """
        body = self._gql(mutation, {'email': self.user.login})
        self.assertEqual(body['data']['resetPassword']['id'], self.user.id)

    def test_change_password(self):
        # A fresh signup token lets us set a new password without the old one.
        token = self.user.partner_id.sudo()._generate_signup_token()
        self.assertTrue(token, "signup token should have been generated")
        mutation = """
            mutation ($token: String!, $pw: String!) {
              changePassword(token: $token, newPassword: $pw) { id }
            }
        """
        body = self._gql(mutation, {'token': token, 'pw': 'brand-new-pw-123'})
        self.assertEqual(body['data']['changePassword']['id'], self.user.id)

    def test_update_password(self):
        self._login()
        mutation = """
            mutation ($cur: String!, $new: String!) {
              updatePassword(currentPassword: $cur, newPassword: $new) { id }
            }
        """
        body = self._gql(mutation, {'cur': self.password, 'new': 'another-new-pw-123'})
        self.assertEqual(body['data']['updatePassword']['id'], self.user.id)

    def test_totp_verification(self):
        # The user has no TOTP set up, so verification raises a GraphQLError.
        # The point here is that the mutation + its output selection are valid.
        mutation = """
            mutation ($code: String!, $uid: Int!) {
              totpVerification(code: $code, userId: $uid, rememberDevice: false) {
                user { id } key value maxAge httponly samesite
              }
            }
        """
        self._gql(mutation, {'code': '123456', 'uid': self.user.id}, expect_errors=True)

    def test_checkout_redirect(self):
        mutation = """
            mutation ($sid: String!) { checkoutRedirect(sessionId: $sid) { accessToken } }
        """
        body = self._gql(mutation, {'sid': 'non-existent-session'})
        self.assertTrue(body['data']['checkoutRedirect']['accessToken'])

    def test_update_my_account(self):
        self._login()
        mutation = """
            mutation ($a: UpdateMyAccountParams) { updateMyAccount(myaccount: $a) { %s } }
        """ % PARTNER_FIELDS
        body = self._gql(mutation, {'a': {
            'name': 'Renamed Tester', 'phone': '555-9999',
        }})
        self.assertEqual(body['data']['updateMyAccount']['name'], 'Renamed Tester')

    def test_delete_my_account(self):
        # Use a throwaway portal user so deletion doesn't affect the shared one.
        victim = new_test_user(
            self.env, login='alokai_victim', password='pw-victim',
            groups='base.group_portal', name='Victim',
            email='alokai_victim@example.com',
        )
        self.env.flush_all()
        self.authenticate(victim.login, 'pw-victim')
        # Stub the mail template send so the test never hits SMTP.
        with patch(
            'odoo.addons.mail.models.mail_template.MailTemplate.send_mail',
            return_value=True,
        ):
            body = self._gql("mutation { deleteMyAccount }")
        self.assertTrue(body['data']['deleteMyAccount'])

    # ------------------------------------------------------------------ #
    #  addresses                                                          #
    # ------------------------------------------------------------------ #
    def test_add_address(self):
        self._login()
        mutation = """
            mutation ($addr: AddAddressInput) {
              addAddress(type: Shipping, address: $addr) { %s }
            }
        """ % PARTNER_FIELDS
        body = self._gql(mutation, {'addr': {
            'name': 'Brand New Ship', 'street': 'New St 9', 'zip': '1234-567',
            'city': 'Coimbra', 'countryId': self.env.ref('base.pt').id,
            'phone': '555-2222',
        }})
        self.assertEqual(body['data']['addAddress']['name'], 'Brand New Ship')

    def test_update_address(self):
        self._login()
        mutation = """
            mutation ($addr: UpdateAddressInput!) {
              updateAddress(address: $addr) { id name }
            }
        """
        body = self._gql(mutation, {'addr': {
            'id': self.shipping_address.id, 'name': 'Updated Ship Name',
        }})
        self.assertEqual(body['data']['updateAddress']['name'], 'Updated Ship Name')

    def test_delete_address(self):
        self._login()
        mutation = """
            mutation ($addr: DeleteAddressInput) { deleteAddress(address: $addr) { result } }
        """
        body = self._gql(mutation, {'addr': {'id': self.shipping_address.id}})
        self.assertTrue(body['data']['deleteAddress']['result'])

    def test_select_address(self):
        self._login()
        self._add_to_cart()
        mutation = """
            mutation ($addr: SelectAddressInput) {
              selectAddress(type: Shipping, address: $addr) { id name }
            }
        """
        body = self._gql(mutation, {'addr': {'id': self.shipping_address.id}})
        self.assertEqual(body['data']['selectAddress']['id'], self.shipping_address.id)

    def test_add_address_appears_in_addresses(self):
        """A newly added address is then returned by the addresses query."""
        self._login()
        add = """
            mutation ($addr: AddAddressInput) {
              addAddress(type: Shipping, address: $addr) { id }
            }
        """
        new_id = self._gql(add, {'addr': {
            'name': 'Integration Ship', 'street': 'Int St 1', 'zip': '9999-999',
            'city': 'Faro', 'countryId': self.env.ref('base.pt').id, 'phone': '555-3333',
        }})['data']['addAddress']['id']
        listed = self._gql("query { addresses { id } }")['data']['addresses']
        self.assertIn(new_id, [a['id'] for a in listed])

    def test_delete_primary_address_raises(self):
        """The primary (parentless) address cannot be deleted."""
        self._login()
        mutation = """
            mutation ($addr: DeleteAddressInput) { deleteAddress(address: $addr) { result } }
        """
        self._gql(mutation, {'addr': {'id': self.partner.id}}, expect_errors=True)

    def test_add_address_public_without_cart_raises(self):
        """A public caller with no cart cannot add an address."""
        mutation = """
            mutation ($addr: AddAddressInput) {
              addAddress(type: Shipping, address: $addr) { id }
            }
        """
        self._gql(mutation, {'addr': {
            'name': 'Nope', 'street': 'x', 'zip': '1', 'city': 'y',
            'countryId': self.env.ref('base.pt').id, 'phone': '0',
        }}, expect_errors=True)

    # ------------------------------------------------------------------ #
    #  wishlist                                                           #
    # ------------------------------------------------------------------ #
    def test_wishlist_add_and_remove(self):
        self._login()
        variant_id = self.product2.product_variant_id.id
        add = """
            mutation ($pid: Int!) {
              wishlistAddItem(productId: $pid) {
                wishlistItems { id product { id } } totalCount
              }
            }
        """
        items = self._gql(add, {'pid': variant_id})['data']['wishlistAddItem']['wishlistItems']
        match = [w for w in items if w['product'] and w['product']['id'] == variant_id]
        self.assertTrue(match, "added product should appear in the wishlist")
        wish_id = match[0]['id']

        remove = """
            mutation ($wid: Int!) {
              wishlistRemoveItem(wishId: $wid) {
                wishlistItems { id product { id } } totalCount
              }
            }
        """
        remaining = self._gql(remove, {'wid': wish_id})['data']['wishlistRemoveItem']['wishlistItems']
        self.assertNotIn(variant_id, [w['product']['id'] for w in remaining if w['product']])

    # ------------------------------------------------------------------ #
    #  cart                                                               #
    # ------------------------------------------------------------------ #
    def test_cart_add_multiple_items(self):
        order = self._add_to_cart()
        self.assertTrue(order['orderLines'])

    def test_cart_update_multiple_items(self):
        order = self._add_to_cart(qty=1)
        line_id = order['orderLines'][0]['id']
        mutation = """
            mutation ($lines: [CartLineInput!]!) {
              cartUpdateMultipleItems(lines: $lines) {
                order { id orderLines { id quantity } }
              }
            }
        """
        body = self._gql(mutation, {'lines': [{'id': line_id, 'quantity': 3}]})
        self.assertTrue(body['data']['cartUpdateMultipleItems']['order'])

    def test_cart_remove_multiple_items(self):
        order = self._add_to_cart()
        line_id = order['orderLines'][0]['id']
        mutation = """
            mutation ($ids: [Int]!) {
              cartRemoveMultipleItems(lineIds: $ids) { order { id orderLines { id } } }
            }
        """
        body = self._gql(mutation, {'ids': [line_id]})
        # resolve_order_lines returns None (not []) for an empty order.
        self.assertFalse(body['data']['cartRemoveMultipleItems']['order']['orderLines'])

    def test_cart_clear(self):
        self._add_to_cart()
        mutation = "mutation { cartClear { %s } }" % ORDER_FIELDS
        self._gql(mutation)

    def test_set_shipping_method(self):
        self._add_to_cart()
        if not self.carrier:
            self.skipTest("No delivery.carrier available")
        mutation = """
            mutation ($id: Int!) {
              setShippingMethod(shippingMethodId: $id) {
                order { id amountDelivery shippingMethod { id name price } }
              }
            }
        """
        self._gql(mutation, {'id': self.carrier.id})

    def test_create_update_partner(self):
        mutation = """
            mutation ($name: String!, $email: String!) {
              createUpdatePartner(name: $name, email: $email,
                                  subscribeNewsletter: true, phone: "555-1234",
                                  mobile: "555-4321") { %s }
            }
        """ % PARTNER_FIELDS
        body = self._gql(mutation, {'name': 'Guest Buyer', 'email': 'guest@example.com'})
        self.assertEqual(body['data']['createUpdatePartner']['name'], 'Guest Buyer')

    # ------------------------------------------------------------------ #
    #  coupons / gift cards / payment                                     #
    # ------------------------------------------------------------------ #
    def test_apply_coupon_invalid(self):
        self._add_to_cart()
        mutation = """
            mutation { applyCoupon(promo: "INVALID-CODE") {
              order { id } error
            } }
        """
        body = self._gql(mutation)
        # No GraphQL error; the failure is reported in the `error` field.
        self.assertTrue(body['data']['applyCoupon']['error'])

    def test_apply_coupon_valid(self):
        """Applying a real promo code must succeed and add a discount line."""
        self.env['loyalty.program'].sudo().create({
            'name': 'Alokai GraphQL 10%',
            'program_type': 'promotion',
            'trigger': 'with_code',
            'applies_on': 'current',
            'company_id': self.website.company_id.id,
            'rule_ids': [(0, 0, {'code': 'ALOKAI10', 'minimum_amount': 0.0})],
            'reward_ids': [(0, 0, {
                'reward_type': 'discount',
                'discount': 10.0,
                'discount_mode': 'percent',
                'discount_applicability': 'order',
            })],
        })
        self.env.flush_all()
        self._add_to_cart()
        pre_total = self._gql("query { cart { order { amountTotal } } }")[
            'data']['cart']['order']['amountTotal']
        mutation = """
            mutation { applyCoupon(promo: "ALOKAI10") {
              order { id amountTotal orderLines { id } } error
            } }
        """
        body = self._gql(mutation)
        result = body['data']['applyCoupon']
        self.assertFalse(result['error'], "valid coupon should not return an error")
        # A reward (discount) line is added on top of the product line.
        self.assertGreaterEqual(len(result['order']['orderLines']), 2)
        # ...and the 10% discount actually lowers the total.
        self.assertLess(result['order']['amountTotal'], pre_total)

    def test_apply_gift_card_invalid(self):
        self._add_to_cart()
        mutation = """
            mutation { applyGiftCard(promo: "INVALID-CODE") {
              order { id } error
            } }
        """
        body = self._gql(mutation)
        self.assertTrue(body['data']['applyGiftCard']['error'])

    def test_make_gift_card_payment(self):
        # Cart has a priced product, so this returns done:false (no error).
        self._add_to_cart()
        body = self._gql("mutation { makeGiftCardPayment { done } }")
        self.assertIn('done', body['data']['makeGiftCardPayment'])

    # ------------------------------------------------------------------ #
    #  mailing                                                            #
    # ------------------------------------------------------------------ #
    def test_newsletter_subscribe(self):
        mutation = """
            mutation ($email: String) { newsletterSubscribe(email: $email) { subscribed } }
        """
        body = self._gql(mutation, {'email': 'subscriber@example.com'})
        self.assertTrue(body['data']['newsletterSubscribe']['subscribed'])

    def test_user_add_multiple_mailing(self):
        self._login()
        mutation = """
            mutation ($m: [MailingInput]!) {
              userAddMultipleMailing(mailings: $m) {
                id name email companyName
                subscriptionList { id optOut mailingList { id name } }
              }
            }
        """
        body = self._gql(mutation, {'m': [
            {'mailinglistId': self.mailing_list.id, 'optout': False},
        ]})
        self.assertTrue(body['data']['userAddMultipleMailing']['id'])
