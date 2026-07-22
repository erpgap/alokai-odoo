# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""One test per GraphQL query, each selecting *every* field of the query.

Selecting all fields is deliberate: a query can break for a single field whose
resolver fails (often after a client customisation), and a narrow selection
would hide it. If a query needs runtime state (an authenticated user, a cart in
the session) the test sets it up first.
"""

from odoo.tests import tagged

from .common import (
    AlokaiGraphQLCommon,
    ATTRIBUTE_FIELDS, BLOG_POST_FIELDS, BLOG_TAG_FIELDS, CATEGORY_FIELDS,
    COUNTRY_FIELDS, HOMEPAGE_FIELDS, INVOICE_FIELDS, MAILING_CONTACT_FIELDS,
    MAILING_LIST_FIELDS, ORDER_FIELDS, PARTNER_FIELDS, PAYMENT_PROVIDER_FIELDS,
    PAYMENT_TRANSACTION_FIELDS, PRODUCT_FIELDS, PRODUCT_VARIANT_FIELDS,
    SHIPPING_METHOD_FIELDS, WEBSITE_MENU_FIELDS, WEBSITE_PAGE_FIELDS,
    WISHLIST_ITEM_FIELDS,
)


@tagged('post_install', '-at_install', 'alokai')
class TestAlokaiQueries(AlokaiGraphQLCommon):

    # ---------------------- helpers --------------------------------- #
    def _add_to_cart(self):
        """Create a cart in the current HTTP session (sets session sale_order_id)."""
        mutation = """
            mutation ($products: [ProductInput!]!) {
              cartAddMultipleItems(products: $products) { order { id } }
            }
        """
        variant = self.product.product_variant_id
        self._gql(mutation, {'products': [{'id': variant.id, 'quantity': 1}]})

    # ---------------------- catalog -------------------------------- #
    def test_categories(self):
        query = """
            query { categories(currentPage: 1, pageSize: 50, sort: {id: ASC}) {
              categories { %s } totalCount
            } }
        """ % CATEGORY_FIELDS
        body = self._gql(query)
        self.assertIn('categories', body['data']['categories'])

    def test_category(self):
        query = "query ($id: Int) { category(id: $id) { %s } }" % CATEGORY_FIELDS
        self._gql(query, {'id': self.category.id})

    def test_category_by_slug(self):
        if not self.category.website_slug:
            self.skipTest("Category has no slug")
        query = "query ($slug: String) { category(slug: $slug) { id slug } }"
        body = self._gql(query, {'slug': self.category.website_slug})
        self.assertEqual(body['data']['category']['id'], self.category.id)

    def test_products(self):
        query = """
            query { products(currentPage: 1, pageSize: 20, sort: {id: ASC}) {
              products { %s }
              totalCount
              attributeValues { id name }
              minPrice maxPrice filterCounts searchUrl
            } }
        """ % PRODUCT_FIELDS
        body = self._gql(query)
        self.assertIn('products', body['data']['products'])

    def test_products_sorted_by_price(self):
        """The price sort uses a different code path (pricelist computation)."""
        query = """
            query { products(pageSize: 5, sort: {price: ASC}) {
              products { id name price } totalCount minPrice maxPrice
            } }
        """
        self._gql(query)

    def test_products_in_stock_filter(self):
        query = """
            query { products(filter: {inStock: true}, pageSize: 5) {
              products { id } totalCount filterCounts
            } }
        """
        self._gql(query)

    def test_product(self):
        query = "query ($id: Int) { product(id: $id) { %s } }" % PRODUCT_FIELDS
        body = self._gql(query, {'id': self.product.id})
        self.assertTrue(body['data']['product'])

    def test_product_by_slug(self):
        query = "query ($slug: String) { product(slug: $slug) { id slug } }"
        body = self._gql(query, {'slug': self.product.website_slug})
        self.assertEqual(body['data']['product']['id'], self.product.id)

    def test_product_by_barcode(self):
        query = "query ($bc: String) { product(barcode: $bc) { id barcode } }"
        body = self._gql(query, {'bc': self.barcode_product.barcode})
        self.assertEqual(body['data']['product']['id'], self.barcode_product.id)

    def test_product_not_found_returns_null(self):
        query = "query ($id: Int) { product(id: $id) { id } }"
        body = self._gql(query, {'id': 999999999})
        self.assertIsNone(body['data']['product'])

    def test_attribute(self):
        if not self.attribute:
            self.skipTest("No product.attribute available")
        query = "query ($id: Int) { attribute(id: $id) { %s } }" % ATTRIBUTE_FIELDS
        self._gql(query, {'id': self.attribute.id})

    def test_product_variant(self):
        query = """
            query ($tmpl: Int) { productVariant(productTemplateId: $tmpl) { %s } }
        """ % PRODUCT_VARIANT_FIELDS
        self._gql(query, {'tmpl': self.product.id})

    # ---------------------- countries ------------------------------ #
    def test_countries(self):
        query = """
            query { countries(currentPage: 1, pageSize: 20, sort: {id: ASC}) {
              countries { %s } totalCount
            } }
        """ % COUNTRY_FIELDS
        self._gql(query)

    def test_country(self):
        query = "query ($id: Int) { country(id: $id) { %s } }" % COUNTRY_FIELDS
        self._gql(query, {'id': self.env.ref('base.pt').id})

    # ---------------------- orders (auth) -------------------------- #
    def test_orders(self):
        self._login()
        query = """
            query { orders(currentPage: 1, pageSize: 10, sort: {id: ASC}) {
              orders { %s } totalCount
            } }
        """ % ORDER_FIELDS
        body = self._gql(query)
        data = body['data']['orders']
        self.assertGreaterEqual(data['totalCount'], 1)
        self.assertIn(self.sale_order.id, [o['id'] for o in data['orders']],
                      "the portal user's order must appear in their order list")

    def test_order(self):
        self._login()
        query = "query ($id: Int) { order(id: $id) { %s } }" % ORDER_FIELDS
        body = self._gql(query, {'id': self.sale_order.id})
        order = body['data']['order']
        self.assertEqual(order['id'], self.sale_order.id)
        self.assertEqual(order['name'], self.sale_order.name)
        self.assertEqual(len(order['orderLines']), len(self.sale_order.order_line))
        self.assertAlmostEqual(order['amountTotal'], self.sale_order.amount_total, places=2)

    def test_order_amounts_consistent(self):
        """amountTotal must equal untaxed + tax (arithmetic sanity)."""
        self._login()
        query = """
            query ($id: Int) { order(id: $id) {
              amountUntaxed amountTax amountTotal
            } }
        """
        o = self._gql(query, {'id': self.sale_order.id})['data']['order']
        self.assertAlmostEqual(o['amountTotal'], o['amountUntaxed'] + o['amountTax'], places=2)
        self.assertGreater(o['amountTotal'], 0)

    def test_delivery_methods(self):
        self._login()
        self._add_to_cart()
        query = "query { deliveryMethods { %s } }" % SHIPPING_METHOD_FIELDS
        self._gql(query)

    # ---------------------- invoices (auth) ------------------------ #
    def test_invoices(self):
        self._login()
        query = """
            query { invoices(currentPage: 1, pageSize: 10, sort: {id: ASC}) {
              invoices { %s } totalCount
            } }
        """ % INVOICE_FIELDS
        self._gql(query)

    def test_invoice(self):
        self._login()
        query = "query ($id: Int) { invoice(id: $id) { %s } }" % INVOICE_FIELDS
        body = self._gql(query, {'id': self.invoice.id})
        invoice = body['data']['invoice']
        self.assertEqual(invoice['id'], self.invoice.id)
        # state is returned as a display label ("Posted"); the invoice is posted.
        self.assertEqual(invoice['state'].lower(), self.invoice.state)
        self.assertAlmostEqual(invoice['amountTotal'], self.invoice.amount_total, places=2)

    # ---------------------- user / addresses ----------------------- #
    def test_partner(self):
        self._login()
        query = "query { partner { %s } }" % PARTNER_FIELDS
        body = self._gql(query)
        self.assertEqual(body['data']['partner']['id'], self.partner.id)

    def test_addresses(self):
        self._login()
        query = "query { addresses { %s } }" % PARTNER_FIELDS
        body = self._gql(query)
        ids = [a['id'] for a in body['data']['addresses']]
        self.assertIn(self.shipping_address.id, ids, "delivery address must be listed")
        self.assertIn(self.invoice_address.id, ids, "invoice address must be listed")

    def test_addresses_filtered(self):
        self._login()
        query = """
            query { addresses(filter: {addressType: [Shipping, Billing]}) { id name addressType } }
        """
        self._gql(query)

    # ---------------------- cart ----------------------------------- #
    def test_cart_empty(self):
        query = """
            query { cart { order { %s } frequentlyBoughtTogether { id name } } }
        """ % ORDER_FIELDS
        body = self._gql(query)
        self.assertIsNone(body['data']['cart']['order'],
                          "a session with no cart must resolve to a null order")

    def test_cart_with_items(self):
        self._add_to_cart()
        query = """
            query { cart { order { %s } frequentlyBoughtTogether { id name } } }
        """ % ORDER_FIELDS
        body = self._gql(query)
        order = body['data']['cart']['order']
        self.assertTrue(order)
        self.assertEqual(len(order['orderLines']), 1, "the added item is in the cart")
        self.assertEqual(order['orderLines'][0]['quantity'], 1)

    # ---------------------- payment -------------------------------- #
    def test_payment_provider(self):
        if not self.provider:
            self.skipTest("No payment.provider available")
        query = "query ($id: Int) { paymentProvider(id: $id) { %s } }" % PAYMENT_PROVIDER_FIELDS
        self._gql(query, {'id': self.provider.id})

    def test_payment_providers(self):
        # Resolver dereferences the cart's company, so a cart must exist.
        self._add_to_cart()
        query = "query { paymentProviders { %s } }" % PAYMENT_PROVIDER_FIELDS
        self._gql(query)

    def test_payment_transaction(self):
        if not self.transaction:
            self.skipTest("No payment.transaction available")
        self._login()
        query = """
            query ($id: Int) { paymentTransaction(id: $id) { %s } }
        """ % PAYMENT_TRANSACTION_FIELDS
        body = self._gql(query, {'id': self.transaction.id})
        tx = body['data']['paymentTransaction']
        self.assertEqual(tx['id'], self.transaction.id)
        self.assertEqual(tx['reference'], self.transaction.reference)
        self.assertAlmostEqual(tx['amount'], self.transaction.amount, places=2)

    def test_payment_transaction_denied_to_public(self):
        """A guest must NOT be able to read another customer's transaction by
        id (no session/ownership) — guards against enumerating all payments."""
        if not self.transaction:
            self.skipTest("No payment.transaction available")
        query = "query ($id: Int) { paymentTransaction(id: $id) { id reference } }"
        body = self._gql(query, {'id': self.transaction.id}, expect_errors=True)
        self.assertIsNone((body.get('data') or {}).get('paymentTransaction'))

    def test_payment_confirmation(self):
        # paymentConfirmation reads the order from the session; create a cart
        # first so the session carries a sale_order_id.
        self._add_to_cart()
        query = "query { paymentConfirmation { order { id name stage } } }"
        self._gql(query)

    # ---------------------- wishlist ------------------------------- #
    def test_wishlist_items(self):
        self._login()
        query = """
            query { wishlistItems { wishlistItems { %s } totalCount } }
        """ % WISHLIST_ITEM_FIELDS
        body = self._gql(query)
        data = body['data']['wishlistItems']
        self.assertGreaterEqual(data['totalCount'], 1)
        self.assertTrue(data['wishlistItems'], "the wishlisted item must be listed")

    # ---------------------- mailing -------------------------------- #
    def test_mailing_contacts(self):
        self._login()
        query = """
            query { mailingContacts(currentPage: 1, pageSize: 20, sort: {id: ASC}) {
              mailingContacts { %s } totalCount
            } }
        """ % MAILING_CONTACT_FIELDS
        self._gql(query)

    def test_mailing_contacts_empty_for_public(self):
        """A guest (blank email) must not receive every email-less contact."""
        query = "query { mailingContacts { mailingContacts { id } totalCount } }"
        body = self._gql(query)
        self.assertEqual(body['data']['mailingContacts']['totalCount'], 0)

    def test_mailing_list(self):
        query = "query ($id: Int) { mailingList(id: $id) { %s } }" % MAILING_LIST_FIELDS
        body = self._gql(query, {'id': self.mailing_list.id})
        self.assertEqual(body['data']['mailingList']['id'], self.mailing_list.id)

    def test_mailing_lists(self):
        query = """
            query { mailingLists(currentPage: 1, pageSize: 20, sort: {id: ASC}) {
              mailingLists { %s } totalCount
            } }
        """ % MAILING_LIST_FIELDS
        self._gql(query)

    # ---------------------- website -------------------------------- #
    def test_website_menu(self):
        query = "query { websiteMenu { %s } }" % WEBSITE_MENU_FIELDS
        self._gql(query)

    def test_website_mega_menu(self):
        query = "query { websiteMegaMenu { %s } }" % WEBSITE_MENU_FIELDS
        self._gql(query)

    def test_website_footer(self):
        query = "query { websiteFooter { %s } }" % WEBSITE_MENU_FIELDS
        self._gql(query)

    def test_website_homepage(self):
        query = "query { websiteHomepage { %s } }" % HOMEPAGE_FIELDS
        self._gql(query)

    # ---------------------- blog ----------------------------------- #
    def test_blog_tags(self):
        query = "query { blogTags { blogTags { %s } totalCount } }" % BLOG_TAG_FIELDS
        self._gql(query)

    def test_blog_post(self):
        query = "query ($id: Int) { blogPost(id: $id) { %s } }" % BLOG_POST_FIELDS
        body = self._gql(query, {'id': self.blog_post.id})
        self.assertEqual(body['data']['blogPost']['id'], self.blog_post.id)

    def test_blog_post_by_slug(self):
        if not self.blog_post.website_slug:
            self.skipTest("Blog post has no slug")
        query = "query ($slug: String) { blogPost(slug: $slug) { id slug } }"
        body = self._gql(query, {'slug': self.blog_post.website_slug})
        self.assertEqual(body['data']['blogPost']['id'], self.blog_post.id)

    def test_blog_posts(self):
        query = """
            query { blogPosts(currentPage: 1, pageSize: 10, sort: {id: ASC}) {
              blogPosts { %s } blogTags { %s } totalCount
            } }
        """ % (BLOG_POST_FIELDS, BLOG_TAG_FIELDS)
        self._gql(query)

    # ---------------------- website pages -------------------------- #
    def test_website_page(self):
        query = "query ($id: Int) { websitePage(id: $id) { %s } }" % WEBSITE_PAGE_FIELDS
        # No record required: a missing id resolves to null (field is nullable).
        self._gql(query, {'id': 0})

    def test_website_pages(self):
        query = """
            query { websitePages(currentPage: 1, pageSize: 20, sort: {id: ASC}) {
              websitePages { %s } totalCount
            } }
        """ % WEBSITE_PAGE_FIELDS
        self._gql(query)

    # ---------------------- access control (negative) -------------- #
    def test_order_denied_to_public(self):
        """A public (unauthenticated) caller must not read another's order."""
        query = "query ($id: Int) { order(id: $id) { id } }"
        self._gql(query, {'id': self.sale_order.id}, expect_errors=True)

    def test_invoice_denied_to_public(self):
        """A public (unauthenticated) caller must not read another's invoice."""
        query = "query ($id: Int) { invoice(id: $id) { id } }"
        self._gql(query, {'id': self.invoice.id}, expect_errors=True)

    # ---------------------- filter / pagination correctness -------- #
    def test_products_filter_by_ids(self):
        """The ``ids`` filter must narrow results to exactly those ids."""
        query = """
            query ($ids: [Int]) {
              products(filter: {ids: $ids}) { products { id } totalCount }
            }
        """
        body = self._gql(query, {'ids': [self.product.id]})
        returned = [p['id'] for p in body['data']['products']['products']]
        self.assertEqual(returned, [self.product.id])

    def test_categories_filter_by_id(self):
        """The category ``id`` filter must narrow results to that category."""
        query = """
            query ($ids: [Int]) {
              categories(filter: {id: $ids}) { categories { id } totalCount }
            }
        """
        body = self._gql(query, {'ids': [self.category.id]})
        returned = [c['id'] for c in body['data']['categories']['categories']]
        self.assertEqual(returned, [self.category.id])

    def test_products_pagination(self):
        """Page 1 and page 2 must return different products."""
        query = """
            query ($page: Int) {
              products(currentPage: $page, pageSize: 1, sort: {id: ASC}) {
                products { id } totalCount
              }
            }
        """
        page1 = self._gql(query, {'page': 1})['data']['products']
        if page1['totalCount'] < 2:
            self.skipTest("Need at least 2 products to test pagination")
        page2 = self._gql(query, {'page': 2})['data']['products']
        self.assertNotEqual(
            page1['products'][0]['id'], page2['products'][0]['id'],
            "Page 2 should not repeat page 1's product",
        )

    def test_products_sort_direction(self):
        """ASC and DESC must actually order the results."""
        query = """
            query ($dir: SortEnum) {
              products(pageSize: 50, sort: {id: $dir}) { products { id } }
            }
        """
        asc = [p['id'] for p in self._gql(query, {'dir': 'ASC'})['data']['products']['products']]
        desc = [p['id'] for p in self._gql(query, {'dir': 'DESC'})['data']['products']['products']]
        if len(asc) < 2:
            self.skipTest("Need at least 2 products to test sorting")
        self.assertEqual(asc, sorted(asc), "ASC should be ascending by id")
        self.assertEqual(desc, sorted(desc, reverse=True), "DESC should be descending by id")

    def test_categories_sort_direction(self):
        """Category id sort must apply in both directions."""
        query = """
            query ($dir: SortEnum) {
              categories(pageSize: 100, sort: {id: $dir}) { categories { id } totalCount }
            }
        """
        asc_data = self._gql(query, {'dir': 'ASC'})['data']['categories']
        if asc_data['totalCount'] < 2 or asc_data['totalCount'] > 100:
            self.skipTest("Need 2..100 categories to assert full ordering")
        asc = [c['id'] for c in asc_data['categories']]
        desc = [c['id'] for c in self._gql(query, {'dir': 'DESC'})['data']['categories']['categories']]
        self.assertEqual(asc, sorted(asc))
        self.assertEqual(desc, sorted(desc, reverse=True))
