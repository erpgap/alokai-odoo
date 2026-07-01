# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""GraphQL tests for the Stripe storefront mutations, with Stripe mocked.

Reuses the graphql_alokai HTTP test harness (portal user, product, website) and
adds an enabled test Stripe provider. The only outbound Stripe call
(``_send_api_request``) is patched, so no network access is needed.
"""

from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.graphql_alokai.tests.common import AlokaiGraphQLCommon

TX_MODEL = 'payment.transaction'


def _fake_stripe_send(tx_self, method, endpoint, **kwargs):
    """Stand in for Stripe: a customer create and a PaymentIntent create."""
    if endpoint == 'customers':
        return {'id': 'cus_mock'}
    if endpoint.startswith('payment_intents'):
        return {
            'id': 'pi_mock_123',
            'client_secret': 'pi_mock_123_secret_abc',
            'status': 'requires_payment_method',
        }
    return {}


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiStripeMutations(AlokaiGraphQLCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.stripe = env['payment.provider'].search([('code', '=', 'stripe')], limit=1)
        if not cls.stripe:
            cls.skip_stripe = True
            return
        cls.skip_stripe = False
        cls.stripe.write({
            'state': 'test',
            'is_published': True,
            'stripe_publishable_key': 'pk_test_alokai',
            'stripe_secret_key': 'sk_test_alokai',
            'stripe_webhook_secret': 'whsec_test_alokai',
        })
        if not cls.stripe.payment_method_ids:
            card = env.ref('payment.payment_method_card', raise_if_not_found=False)
            if card:
                cls.stripe.payment_method_ids = [(4, card.id)]
        env.flush_all()

    def setUp(self):
        super().setUp()
        if self.skip_stripe:
            self.skipTest("No Stripe payment provider available")

    def _add_to_cart(self):
        mutation = """
            mutation ($p: [ProductInput!]!) {
              cartAddMultipleItems(products: $p) { order { id amountTotal } }
            }
        """
        variant = self.product.product_variant_id
        self._gql(mutation, {'p': [{'id': variant.id, 'quantity': 1}]})

    def _select_shipping(self):
        if not self.carrier:
            self.skipTest("No delivery carrier available to ready the cart")
        mutation = """
            mutation ($id: Int!) {
              setShippingMethod(shippingMethodId: $id) { order { id } }
            }
        """
        self._gql(mutation, {'id': self.carrier.id})

    def test_stripe_inline_form_values(self):
        """Returns the values to render the widget — amount from the cart."""
        self._add_to_cart()
        mutation = """
            mutation ($id: Int!) {
              stripeGetInlineFormValues(providerId: $id) { stripeGetInlineFormValues }
            }
        """
        body = self._gql(mutation, {'id': self.stripe.id})
        values = body['data']['stripeGetInlineFormValues']['stripeGetInlineFormValues']
        self.assertEqual(values['publishable_key'], 'pk_test_alokai')
        self.assertGreater(values['minor_amount'], 0)
        self.assertTrue(values['currency_name'])
        self.assertTrue(values['payment_methods'])
        self.assertTrue(values['api_version'])

    def test_stripe_transaction_opens_payment(self):
        """Opening a payment returns the client secret and flags the tx."""
        self._add_to_cart()
        self._select_shipping()
        mutation = """
            mutation ($id: Int!) { stripeTransaction(providerId: $id) { transaction } }
        """
        cls = type(self.env[TX_MODEL])
        with patch.object(cls, '_send_api_request', _fake_stripe_send):
            body = self._gql(mutation, {'id': self.stripe.id})

        tx_values = body['data']['stripeTransaction']['transaction']
        self.assertEqual(tx_values['client_secret'], 'pi_mock_123_secret_abc')
        self.assertIn('/payment/stripe/return', tx_values['return_url'])

        tx = self.env[TX_MODEL].sudo().search(
            [('reference', '=', tx_values['reference'])], limit=1)
        self.assertTrue(tx.created_on_alokai, "tx should be flagged as storefront-originated")
        self.assertEqual(tx.provider_reference, 'pi_mock_123',
                         "PaymentIntent id should be stored for reconciliation")

    def test_stripe_provider_required(self):
        """A non-Stripe / unknown provider id is rejected cleanly."""
        self._add_to_cart()
        mutation = """
            mutation ($id: Int!) {
              stripeGetInlineFormValues(providerId: $id) { stripeGetInlineFormValues }
            }
        """
        self._gql(mutation, {'id': 0}, expect_errors=True)
