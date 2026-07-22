# -*- coding: utf-8 -*-
# Copyright 2024-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

{
    'name': 'Alokai - Stripe Payment',
    'version': '19.0.1.0.0',
    'category': 'Website/eCommerce',
    'summary': 'Pay Alokai (Vue Storefront) orders with Stripe, settled and '
               'confirmed by Odoo.',
    'description': """
Alokai - Stripe Payment
=======================

Lets the Alokai (Vue Storefront) headless frontend take Stripe payments while
Odoo stays the single source of truth for the order, the payment and the
invoice. Card data never touches Odoo or the storefront server: it goes
straight from the shopper's browser to Stripe (PCI SAQ-A).

This module is OPTIONAL. Install it only for customers that use Stripe; the
storefront API (``graphql_alokai``) works without it, and the Stripe-specific
operations simply do not appear in the GraphQL schema when it is not installed.


How a payment flows
-------------------

1. CHECKOUT (browser ⇄ Odoo)
   On the payment step, the storefront asks Odoo for the data needed to render
   the Stripe payment form: the publishable key, the amount and currency (always
   taken from the Odoo cart, never trusted from the browser), and the list of
   enabled payment methods. The storefront mounts the Stripe payment widget with
   these values. No money has moved yet.

2. PAY (browser ⇄ Odoo ⇄ Stripe)
   When the shopper submits, the storefront asks Odoo to open a payment. Odoo
   creates a draft payment record bound to the cart and its amount, asks Stripe
   to create a PaymentIntent for that exact amount, and hands the resulting
   one-time client secret back to the storefront. The order is NOT confirmed at
   this point.

3. AUTHORISE (browser ⇄ Stripe)
   The storefront confirms the PaymentIntent directly with Stripe using the
   client secret. Stripe runs any required Strong Customer Authentication (3-D
   Secure) in the browser. The card details never reach Odoo.

4. CONFIRMATION — three independent layers (Stripe ⇄ Odoo)
   The order is confirmed and the invoice created ONLY when Odoo hears the
   result directly from Stripe — never because the storefront says so. Three
   independent channels make sure that result is never lost:

   a. Webhook (authoritative). Stripe sends a signed, server-to-server webhook
      to Odoo as soon as the payment settles. This works even if the shopper
      closes the tab. Odoo verifies the signature and confirms the order and
      invoice. THIS CHANNEL IS MANDATORY and must be configured in the Stripe
      dashboard.

   b. Return redirect (convenience + speed). After authorisation Stripe sends
      the shopper's browser back to Odoo, which processes the same result and
      then bounces the browser to the storefront's success or error page.

   c. Reconciliation safety net. A scheduled job periodically re-checks, against
      Stripe, any storefront payment that succeeded at Stripe but whose webhook
      and redirect both failed to reach Odoo, and confirms it. This is the belt
      to the webhook's braces so a payment is never silently lost.

   All three converge on the same idempotent processing step, so whichever
   arrives first confirms the order and the others are harmless no-ops.

5. THANK-YOU PAGE (browser ⇄ Odoo)
   The storefront's confirmation page reads the resulting order and its payment
   state back from Odoo to show the shopper the outcome. It only displays state;
   it never decides it.


Why Odoo orchestrates instead of the storefront charging Stripe alone
--------------------------------------------------------------------
* The charge amount is always derived from the Odoo order, so a tampered or
  stale browser amount can never be charged.
* Confirmation comes from Stripe directly (webhook), so a lost or forged
  "success" message from the browser can never confirm or fail an order.
* Odoo's standard payment machinery handles order confirmation, invoicing,
  captures, refunds and accounting — none of it is re-implemented elsewhere.
""",
    'author': 'ERPGAP',
    'website': 'https://www.erpgap.com/alokai/',
    'category': 'Website/eCommerce',
    'maintainer': 'ERPGAP',
    'license': 'LGPL-3',
    'depends': [
        'payment_stripe',
        'graphql_alokai',
    ],
    'data': [
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
