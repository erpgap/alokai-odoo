# Alokai — Odoo Backend Modules

Headless eCommerce backend that exposes [Odoo](https://www.odoo.com/) through a
GraphQL API for the [Alokai](https://alokai.com/) (Vue Storefront) frontend.

## Overview

Alokai is a lightning-fast frontend platform for headless commerce. A headless
architecture decouples your storefront from the content management system and
the backend, giving you a fast, fully customisable shopping experience without
giving up a proven ERP behind it.

Odoo is the ultimate open source ERP, used by millions of companies around the
world — so it is a match made in open source heaven. These modules turn a
standard Odoo instance into the backend for an Alokai storefront: catalog,
cart, checkout, customer accounts and orders are all served over GraphQL.

This is **not** another sync between Odoo and a separate eCommerce database.
Data lives in Odoo only, and Odoo remains the single source of truth.

## Modules

| Module | Description |
| --- | --- |
| [`graphql_alokai`](graphql_alokai/) | Core module. Exposes the GraphQL endpoint and all storefront queries and mutations. |
| [`graphql_alokai_demo`](graphql_alokai_demo/) | Example extension showing how to extend an existing GraphQL type/model. For learning only, not for production. |
| [`payment_stripe_alokai`](payment_stripe_alokai/) | Optional. Take Stripe payments on the storefront while Odoo stays the source of truth for the order, payment and invoice. |
| [`graphql_base`](graphql_base/) | Dependency from OCA's [rest-framework](https://github.com/erpgap/rest-framework) (included as a submodule). |

## Features

- **GraphQL endpoint** at `/graphql/alokai`, with a GraphiQL IDE for development.
- **Storefront queries & mutations**: products, categories, blog, cart,
  checkout, wishlist, addresses, orders and user account.
- **Redis-backed stock cache** kept in sync by lightweight crons (incremental
  "dirty" updates plus a daily full resync).
- **Redis slug sync** providing a dynamic-route fallback for records created
  after a storefront build.
- **On-demand frontend cache invalidation** when records change.
- **Merchandising**: frequently-bought-together and product popularity.
- **Stripe payments** (optional module) with webhook, return-redirect and a
  reconciliation safety net so a payment is never silently lost.

## Requirements

- Odoo 19.0
- Redis (for the stock and slug cache)
- Python packages (see [`requirements.txt`](requirements.txt)):
  `graphene`, `graphql-server==3.0.0b7`, `redis`, `markdown`

## Installation

1. Clone the repository into your Odoo add-ons directory, including submodules:

   ```bash
   git clone --recurse-submodules https://github.com/erpgap/alokai-odoo.git
   ```

   If you already cloned without `--recurse-submodules`, run:

   ```bash
   git submodule update --init --recursive
   ```

2. Install the Python dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Restart Odoo and update the Apps list so the modules appear in the UI.
4. Find **Alokai** in the Apps store and click **Install** (this pulls in
   `graphql_base` and the other dependencies automatically).
5. Optionally install **Alokai - Stripe Payment** if you use Stripe.
6. Spin up your storefront with the
   [Alokai–Odoo integration](https://github.com/vuestorefront-community/odoo).

For full setup instructions see the
[Alokai–Odoo documentation](https://docs.alokai.com/odoo/).

## Configuration

Go to **Website → Settings → Vue Storefront** and set:

- **Payment Return Url**
- **Cache Invalidation Url**
- **Cache Invalidation Key**
- **Web Base Url**

### System parameters

- `alokai_disable_redis_stock` — disables the stock update from Odoo to Redis.
  Useful for testing, and for some production edge cases.

## Development

The GraphiQL IDE at `/graphql/alokai` is the quickest way to explore the schema
and try queries. To extend the API with your own types or fields, see
[`graphql_alokai_demo`](graphql_alokai_demo/) — `graphql/product.py` shows how
to extend an existing type/model.

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes
before submitting a pull request, and keep commits scoped and descriptive.

## License

Licensed under [LGPL-3.0 or later](http://www.gnu.org/licenses/lgpl).

Copyright © 2021–2026 ERPGAP / PROMPTEQUATION LDA.

## Support

To report a problem, please [contact us](https://www.erpgap.com/page/contactus/).

Commercial support is available.
