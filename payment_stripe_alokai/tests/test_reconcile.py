# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Model-level tests for the Stripe ↔ Alokai safety net and payload override.

Stripe is fully mocked: the only outbound call (``_send_api_request``) is
patched, so these tests never touch the network. They verify the custom logic
this module adds — the reconciliation cron and the PaymentIntent payload — not
Odoo's own (already-tested) Stripe processing.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.addons.payment_stripe import const
from odoo.addons.payment_stripe.tests.common import StripeCommon

TX_MODEL = 'payment.transaction'


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiStripeReconcile(StripeCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # StripeCommon clears the provider's methods; give it the card method.
        cls.stripe.payment_method_ids = [(6, 0, cls.payment_method.ids)]

    # ------------------------------------------------------------------ #
    #  helpers                                                            #
    # ------------------------------------------------------------------ #
    def _make_tx(self, ref, **vals):
        values = {
            'provider_id': self.stripe.id,
            'payment_method_id': self.payment_method_id,
            'reference': ref,
            'amount': 50.0,
            'currency_id': self.currency.id,
            'partner_id': self.partner.id,
            'operation': 'online_direct',
            'state': 'draft',
            'created_on_alokai': True,
            'provider_reference': f'pi_{ref}',
        }
        values.update(vals)
        return self.env[TX_MODEL].create(values)

    def _backdate(self, tx, minutes):
        old = fields.Datetime.now() - timedelta(minutes=minutes)
        self.env.cr.execute(
            "UPDATE payment_transaction SET create_date = %s WHERE id = %s",
            (old, tx.id),
        )
        tx.invalidate_recordset(['create_date'])

    def _run_cron(self, intents):
        """Run the reconcile cron with Stripe's intent lookups mocked.

        :param dict intents: maps PaymentIntent id -> intent dict to return.
        :return: list of (tx_id, intent_status) that were sent to _process.
        """
        processed = []

        def fake_send(tx_self, method, endpoint, **kwargs):
            # Unknown intents (e.g. unrelated transactions already in the DB the
            # aged-out sweep may pick up) look like an abandoned payment, so the
            # test only exercises the intents it explicitly sets up.
            return intents.get(
                endpoint.split('/')[-1], {'status': 'requires_payment_method'})

        def fake_process(tx_self, provider_code, payment_data):
            processed.append(
                (tx_self.id, payment_data.get('payment_intent', {}).get('status')))

        cls = type(self.env[TX_MODEL])
        with patch.object(cls, '_send_api_request', fake_send), \
             patch.object(cls, '_process', fake_process):
            self.env[TX_MODEL]._cron_alokai_stripe_reconcile()
        return processed

    # ------------------------------------------------------------------ #
    #  reconciliation cron                                                #
    # ------------------------------------------------------------------ #
    def test_reconcile_confirms_succeeded_payment(self):
        """A draft storefront payment that succeeded at Stripe is processed."""
        tx = self._make_tx('OK')
        self._backdate(tx, minutes=30)
        processed = self._run_cron({'pi_OK': {
            'id': 'pi_OK', 'status': 'succeeded', 'payment_method': {'id': 'pm_1'}}})
        self.assertIn((tx.id, 'succeeded'), processed)

    def test_reconcile_ignores_unpaid_payment(self):
        """A payment still awaiting card details is left untouched."""
        tx = self._make_tx('PENDING')
        self._backdate(tx, minutes=30)
        processed = self._run_cron({'pi_PENDING': {
            'id': 'pi_PENDING', 'status': 'requires_payment_method'}})
        self.assertNotIn(tx.id, [p[0] for p in processed])

    def test_reconcile_ignores_too_recent_payment(self):
        """Payments younger than the grace period are not polled yet."""
        tx = self._make_tx('FRESH')
        self._backdate(tx, minutes=2)
        processed = self._run_cron({'pi_FRESH': {
            'id': 'pi_FRESH', 'status': 'succeeded', 'payment_method': {'id': 'pm_1'}}})
        self.assertNotIn(tx.id, [p[0] for p in processed])

    def test_reconcile_ignores_non_alokai_payment(self):
        """Payments not created from the storefront are out of scope."""
        tx = self._make_tx('BACKEND', created_on_alokai=False)
        self._backdate(tx, minutes=30)
        processed = self._run_cron({'pi_BACKEND': {
            'id': 'pi_BACKEND', 'status': 'succeeded', 'payment_method': {'id': 'pm_1'}}})
        self.assertNotIn(tx.id, [p[0] for p in processed])

    def test_reconcile_ignores_already_done_payment(self):
        """Confirmed payments are not re-processed."""
        tx = self._make_tx('DONE', state='done')
        self._backdate(tx, minutes=30)
        processed = self._run_cron({'pi_DONE': {
            'id': 'pi_DONE', 'status': 'succeeded', 'payment_method': {'id': 'pm_1'}}})
        self.assertNotIn(tx.id, [p[0] for p in processed])

    def test_recovery_posts_note_on_order(self):
        """When the safety net recovers a payment the webhook/return missed, it
        posts an internal note on the linked order for manual review."""
        order = self.env['sale.order'].create({'partner_id': self.partner.id})
        tx = self._make_tx('LATE', sale_order_ids=[(6, 0, order.ids)])
        self._backdate(tx, minutes=30)
        processed = self._run_cron({'pi_LATE': {
            'id': 'pi_LATE', 'status': 'succeeded', 'payment_method': {'id': 'pm_1'}}})
        self.assertIn((tx.id, 'succeeded'), processed)
        self.assertTrue(
            any('reconciliation safety net' in (m.body or '') for m in order.message_ids),
            "a recovered payment must be flagged on the order chatter")

    # ------------------------------------------------------------------ #
    #  PaymentIntent payload                                              #
    # ------------------------------------------------------------------ #
    def test_payload_alokai_accepts_all_methods(self):
        """A storefront PaymentIntent allows every enabled Stripe method."""
        tx = self._make_tx('PAYLOAD')
        cls = type(self.env[TX_MODEL])
        with patch.object(cls, '_send_api_request',
                          lambda *a, **k: {'id': 'cus_mock'}):
            payload = tx._stripe_prepare_payment_intent_payload()
        expected = [
            const.PAYMENT_METHODS_MAPPING.get(c, c)
            for c in self.stripe.payment_method_ids.mapped('code')
        ]
        self.assertEqual(payload['payment_method_types[]'], expected)
        self.assertIsInstance(payload['payment_method_types[]'], list)

    def test_payload_backend_unchanged(self):
        """A non-storefront PaymentIntent keeps Odoo's default single method."""
        tx = self._make_tx('PAYLOAD_BACKEND', created_on_alokai=False)
        cls = type(self.env[TX_MODEL])
        with patch.object(cls, '_send_api_request',
                          lambda *a, **k: {'id': 'cus_mock'}):
            payload = tx._stripe_prepare_payment_intent_payload()
        # Odoo's default sets a single (string) payment method type.
        self.assertNotIsInstance(payload['payment_method_types[]'], list)
