# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Shared test infrastructure for the Alokai GraphQL API.

The goal of these tests is to exercise *every* query and mutation, selecting
*every* field on each query, so that a broken field resolver (e.g. after a
client customisation on top of this module) shows up as a failing test instead
of a runtime error discovered in production.

The :class:`AlokaiGraphQLCommon` base class:
  * builds a self-contained data set (portal user + partner, sale order,
    invoice, wishlist, mailing list, payment provider/transaction, blog post)
    so authenticated queries return real rows and run every resolver;
  * exposes ``_gql`` which POSTs to ``/graphql/alokai`` and asserts the
    response is a valid GraphQL response without ``errors``.

GraphQL field/argument names are camelCase because graphene's
``auto_camelcase`` is enabled (e.g. ``createUpdatePartner``, ``totalCount``).
The big ``*_FIELDS`` constants below are the full per-type selection sets and
are reused across the query and mutation tests.
"""

import json

from odoo.tests.common import HttpCase, new_test_user


# --------------------------------------------------------------------------- #
#  Field selection blocks (camelCase, every field of every type)              #
#  Self-referential relations (Product -> Product, Partner -> Partner, ...)   #
#  use a reduced "*_MIN" selection to avoid infinite nesting while still      #
#  triggering the resolver.                                                   #
# --------------------------------------------------------------------------- #

STATE_FIELDS = "id name code"
COUNTRY_FIELDS = f"id name code states {{ {STATE_FIELDS} }}"
CURRENCY_FIELDS = "id name symbol"
PRICELIST_FIELDS = f"id name currency {{ {CURRENCY_FIELDS} }}"

PARTNER_MIN = "id name email phone isCompany"

PARTNER_FIELDS = f"""
    id name street street2 city zip email phone addressType isCompany
    image imageFilename imageUrl vat isPublic
    companyName companyRegNo
    country {{ {COUNTRY_FIELDS} }}
    state {{ {STATE_FIELDS} }}
    billingAddress {{ {PARTNER_MIN} }}
    shippingAddress {{ {PARTNER_MIN} }}
    company {{ {PARTNER_MIN} }}
    contacts {{ {PARTNER_MIN} }}
    parentId {{ {PARTNER_MIN} }}
    publicPricelist {{ {PRICELIST_FIELDS} }}
    currentPricelist {{ {PRICELIST_FIELDS} }}
"""

COMPANY_FIELDS = f"""
    id name street street2 city zip email phone image imageFilename
    imageUrl vat socialTwitter socialFacebook socialGithub socialLinkedin
    socialYoutube socialInstagram
    country {{ {COUNTRY_FIELDS} }}
    state {{ {STATE_FIELDS} }}
"""

ATTRIBUTE_MIN = "id name displayType variantCreateMode filterVisibility"
ATTRIBUTE_VALUE_FIELDS = f"""
    id name displayType htmlColor search priceExtra
    attribute {{ {ATTRIBUTE_MIN} }}
"""
ATTRIBUTE_FIELDS = f"""
    id name displayType variantCreateMode filterVisibility
    values {{ {ATTRIBUTE_VALUE_FIELDS} }}
"""

PRODUCT_IMAGE_FIELDS = "id name image imageFilename imageUrl video"
RIBBON_FIELDS = "id html textColor htmlClass bgColor displayName"
PRODUCT_TAG_FIELDS = (
    "name color backgroundColor visibleOnEcommerce image imageFilename imageUrl"
)
CATEGORY_MIN = "id name slug"
WEBSITE_PAGE_MIN = "id name websiteUrl"

PRODUCT_MIN = "id name sku slug price"

PRODUCT_FIELDS = f"""
    id typeId visibility status name displayName sku barcode description
    websiteDescription weight metaTitle metaKeyword metaDescription metaImage
    image smallImage imageFilename imageUrl thumbnail allowOutOfStock
    showAvailableQty outOfStockMessage isInStock isInWishlist qty slug
    combinationInfoVariant variantPrice variantPriceAfterDiscount
    variantHasDiscountedPrice isVariantPossible combinationInfo price
    jsonLd breadcrumb jsonLdBreadcrumb ratingCount ratingAvg
    currency {{ {CURRENCY_FIELDS} }}
    categories {{ {CATEGORY_MIN} }}
    ribbon {{ {RIBBON_FIELDS} }}
    mediaGallery {{ {PRODUCT_IMAGE_FIELDS} }}
    tags {{ {PRODUCT_TAG_FIELDS} }}
    alternativeProducts {{ {PRODUCT_MIN} }}
    accessoryProducts {{ {PRODUCT_MIN} }}
    frequentlyBoughtTogether {{ {PRODUCT_MIN} }}
    variantAttributeValues {{ {ATTRIBUTE_VALUE_FIELDS} }}
    productTemplate {{ {PRODUCT_MIN} }}
    attributeValues {{ {ATTRIBUTE_VALUE_FIELDS} }}
    productVariants {{ {PRODUCT_MIN} }}
    firstVariant {{ {PRODUCT_MIN} }}
    alokaiPages {{ {WEBSITE_PAGE_MIN} }}
"""

CATEGORY_FIELDS = f"""
    id name image imageFilename imageUrl slug jsonLd metaTitle metaKeyword
    metaDescription metaImage breadcrumb
    parent {{ {CATEGORY_MIN} }}
    childs {{ {CATEGORY_MIN} }}
"""

PAYMENT_FIELDS = "id name amount paymentReference"
PAYMENT_TRANSACTION_FIELDS = f"""
    id reference amount provider providerReference state
    payment {{ {PAYMENT_FIELDS} }}
    currency {{ {CURRENCY_FIELDS} }}
    company {{ {PARTNER_MIN} }}
    customer {{ {PARTNER_MIN} }}
"""

COUPON_FIELDS = "id code"
GIFT_CARD_FIELDS = "id code"
ORDER_LINE_FIELDS = f"""
    id name quantity priceUnit priceSubtotal priceTotal priceTax shopWarning
    product {{ {PRODUCT_MIN} }}
    giftCard {{ {GIFT_CARD_FIELDS} }}
    coupon {{ {COUPON_FIELDS} }}
"""
SHIPPING_METHOD_FIELDS = f"id name price product {{ {PRODUCT_MIN} }}"

ORDER_FIELDS = f"""
    id name dateOrder taxTotals amountUntaxed amountTax amountTotal
    amountDelivery amountSubtotal amountDiscounts amountGiftCards currencyRate
    stage orderUrl clientOrderRef invoiceStatus invoiceCount cartQuantity
    partner {{ {PARTNER_MIN} }}
    partnerShipping {{ {PARTNER_MIN} }}
    partnerInvoice {{ {PARTNER_MIN} }}
    shippingMethod {{ {SHIPPING_METHOD_FIELDS} }}
    currency {{ {CURRENCY_FIELDS} }}
    orderLines {{ {ORDER_LINE_FIELDS} }}
    websiteOrderLine {{ {ORDER_LINE_FIELDS} }}
    transactions {{ {PAYMENT_TRANSACTION_FIELDS} }}
    lastTransaction {{ {PAYMENT_TRANSACTION_FIELDS} }}
    coupons {{ {COUPON_FIELDS} }}
    giftCards {{ {GIFT_CARD_FIELDS} }}
    reportOrderLine {{ {ORDER_LINE_FIELDS} }}
"""

INVOICE_LINE_FIELDS = f"""
    id name quantity priceUnit priceSubtotal priceTotal
    product {{ {PRODUCT_MIN} }}
"""
INVOICE_FIELDS = f"""
    id name invoiceDate invoiceDateDue taxTotals amountUntaxed amountTax
    amountTotal amountResidual state invoiceUrl
    partner {{ {PARTNER_MIN} }}
    partnerShipping {{ {PARTNER_MIN} }}
    currency {{ {CURRENCY_FIELDS} }}
    invoiceLines {{ {INVOICE_LINE_FIELDS} }}
    transactions {{ {PAYMENT_TRANSACTION_FIELDS} }}
"""

PAYMENT_METHOD_MIN = "id name code active"
PAYMENT_PROVIDER_FIELDS = f"""
    id name code
    paymentMethods {{ {PAYMENT_METHOD_MIN} }}
"""
PAYMENT_METHOD_FIELDS = f"""
    id name sequence code active image imagePaymentForm imageFilename imageUrl
    providers {{ id name code }}
    brands {{ {PAYMENT_METHOD_MIN} }}
"""

WISHLIST_ITEM_FIELDS = f"""
    id
    partner {{ {PARTNER_MIN} }}
    product {{ {PRODUCT_MIN} }}
"""

USER_FIELDS = f"""
    id name email totpRequired
    partner {{ {PARTNER_MIN} }}
"""

MAILING_LIST_FIELDS = "id name"
MAILING_CONTACT_FIELDS = f"""
    id name email companyName
    subscriptionList {{ id optOut mailingList {{ {MAILING_LIST_FIELDS} }} }}
"""

WEBSITE_FIELDS = f"""
    id name
    company {{ {COMPANY_FIELDS} }}
    publicUser {{ {USER_FIELDS} }}
"""

WEBSITE_MENU_MIN = "id name url sequence isFooter isMegaMenu"
WEBSITE_MENU_IMAGE_FIELDS = (
    "id image imageFilename imageUrl tag title subtitle sequence textColor "
    "buttonText buttonUrl"
)
WEBSITE_MENU_FIELDS = f"""
    id name url isFooter isMegaMenu sequence
    parent {{ {WEBSITE_MENU_MIN} }}
    childs {{ {WEBSITE_MENU_MIN} }}
    images {{ {WEBSITE_MENU_IMAGE_FIELDS} }}
"""

WEBSITE_PAGE_FIELDS = f"""
    id pageType name websiteUrl isPublished publishingDate content
    website {{ {WEBSITE_FIELDS} }}
"""

BLOG_TAG_FIELDS = "id name slug"
BLOG_POST_FIELDS = f"""
    id image imageFilename imageUrl name publishedDate content teaser slug
    jsonLd
    author {{ {PARTNER_MIN} }}
    tags {{ {BLOG_TAG_FIELDS} }}
"""

LEAD_FIELDS = "id name email phone company subject message"

HOMEPAGE_FIELDS = (
    "metaTitle metaKeyword metaDescription metaImage metaImageFilename jsonLd"
)

PRODUCT_VARIANT_FIELDS = f"""
    productTemplateId displayName displayImage price listPrice
    hasDiscountedPrice isCombinationPossible
    product {{ {PRODUCT_MIN} }}
"""


class AlokaiGraphQLCommon(HttpCase):
    """Base class: builds data and provides the ``_gql`` HTTP helper."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env

        cls.website = env['website'].search([], limit=1)

        # Allow uninvited signup so the `register` mutation works.
        env['ir.config_parameter'].sudo().set_param(
            'auth_signup.invitation_scope', 'b2c')

        # --- Portal user + partner (for authenticated queries/mutations) --- #
        cls.password = 'alokai-test-pw'
        cls.user = new_test_user(
            env, login='alokai_portal_user', password=cls.password,
            groups='base.group_portal', name='Alokai Portal Tester',
            email='alokai_portal_user@example.com',
        )
        cls.partner = cls.user.partner_id
        cls.partner.write({
            'phone': '+351 000 000 000',
            'street': 'Rua de Teste 1',
            'city': 'Lisboa',
            'zip': '1000-001',
            'country_id': env.ref('base.pt').id,
        })

        # Child delivery + invoice addresses owned by the portal partner.
        cls.shipping_address = env['res.partner'].create({
            'name': 'Shipping Addr', 'type': 'delivery', 'parent_id': cls.partner.id,
            'street': 'Ship St 2', 'city': 'Porto', 'zip': '4000-002',
            'country_id': env.ref('base.pt').id, 'phone': '+351 111 111 111',
        })
        cls.invoice_address = env['res.partner'].create({
            'name': 'Billing Addr', 'type': 'invoice', 'parent_id': cls.partner.id,
            'street': 'Bill St 3', 'city': 'Braga', 'zip': '4700-003',
            'country_id': env.ref('base.pt').id, 'phone': '+351 222 222 222',
        })

        # --- A published product (template) to query against --- #
        cls.product = env['product.template'].search(
            [('is_published', '=', True), ('sale_ok', '=', True)], limit=1)
        if not cls.product:
            cls.product = env['product.template'].search([('sale_ok', '=', True)], limit=1)
        if not cls.product:
            cls.product = env['product.template'].create({
                'name': 'Alokai Test Product', 'list_price': 9.99,
                'is_published': True,
            })

        # A dedicated single-variant published product carrying a barcode, so
        # the barcode-lookup path is deterministic (template.barcode is only
        # writable on single-variant templates).
        cls.barcode_product = env['product.template'].create({
            'name': 'Alokai Barcode Product',
            'list_price': 5.0,
            'is_published': True,
            'barcode': 'ALOKAI-TEST-BARCODE',
        })

        # A second published product (wishlist add, pagination, etc.).
        cls.product2 = env['product.template'].search(
            [('is_published', '=', True), ('sale_ok', '=', True),
             ('id', '!=', cls.product.id)], limit=1)
        if not cls.product2:
            cls.product2 = env['product.template'].create({
                'name': 'Alokai Test Product 2', 'list_price': 19.99,
                'is_published': True,
            })

        cls.attribute = env['product.attribute'].search([], limit=1)
        if not cls.attribute:
            cls.attribute = env['product.attribute'].create({
                'name': 'Alokai Test Attribute',
                'value_ids': [(0, 0, {'name': 'Value A'}), (0, 0, {'name': 'Value B'})],
            })

        cls.category = env['product.public.category'].search([], limit=1)
        if not cls.category:
            cls.category = env['product.public.category'].create({'name': 'Test Cat'})

        # --- A confirmed sale order owned by the portal partner --- #
        cls.sale_order = env['sale.order'].create({
            'partner_id': cls.partner.id,
            'website_id': cls.website.id,
            'order_line': [(0, 0, {
                'product_id': cls.product.product_variant_id.id,
                'product_uom_qty': 1,
            })],
        })
        cls.sale_order.action_confirm()
        # Make sure the portal partner follows the order so it passes the
        # ``message_partner_ids child_of`` access filter used by the resolvers.
        cls.sale_order.message_subscribe(partner_ids=cls.partner.ids)

        # --- A payment provider + transaction (payment queries) --- #
        cls.provider = env['payment.provider'].search(
            [('state', 'in', ('enabled', 'test'))], limit=1)
        if not cls.provider:
            cls.provider = env['payment.provider'].search([], limit=1)
            if cls.provider:
                cls.provider.state = 'test'
        cls.transaction = False
        if cls.provider:
            cls.transaction = env['payment.transaction'].create({
                'provider_id': cls.provider.id,
                'payment_method_id': cls.provider.payment_method_ids[:1].id
                or env['payment.method'].search([], limit=1).id,
                'amount': 10.0,
                'currency_id': cls.sale_order.currency_id.id,
                'partner_id': cls.partner.id,
                'reference': 'ALOKAI-TEST-TX',
                'sale_order_ids': [(6, 0, cls.sale_order.ids)],
            })

        # --- A posted customer invoice (portal users only see posted ones) --- #
        cls.invoice = env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': cls.partner.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Alokai test line',
                'quantity': 1,
                'price_unit': 10.0,
            })],
        })
        cls.invoice.action_post()
        cls.invoice.message_subscribe(partner_ids=cls.partner.ids)

        # --- A wishlist item --- #
        cls.wishlist = env['product.wishlist'].create({
            'partner_id': cls.partner.id,
            'product_id': cls.product.product_variant_id.id,
            'website_id': cls.website.id,
        })

        # --- A public mailing list (newsletter) --- #
        cls.mailing_list = env['mailing.list'].create({
            'name': 'Alokai Test Newsletter', 'is_public': True,
        })
        cls.website.alokai_mailing_list_id = cls.mailing_list.id

        # --- A published blog post (blog queries) --- #
        cls.blog_post = env['blog.post'].search([('is_published', '=', True)], limit=1)
        if not cls.blog_post:
            blog = env['blog.blog'].search([], limit=1) or env['blog.blog'].create(
                {'name': 'Alokai Test Blog'})
            cls.blog_post = env['blog.post'].create({
                'name': 'Alokai Test Post', 'blog_id': blog.id, 'is_published': True,
            })

        # --- A published delivery carrier (setShippingMethod mutation) --- #
        cls.carrier = env['delivery.carrier'].search(
            [('is_published', '=', True)], limit=1)
        if not cls.carrier:
            cls.carrier = env['delivery.carrier'].search([], limit=1)
            if cls.carrier:
                cls.carrier.is_published = True

        env.flush_all()

    # ------------------------------------------------------------------ #
    #  HTTP helper                                                        #
    # ------------------------------------------------------------------ #
    def _gql(self, query, variables=None, expect_errors=False):
        """POST a GraphQL document and return the parsed JSON body.

        Asserts HTTP 200 and, unless ``expect_errors`` is set, that the
        response contains ``data`` and no ``errors`` (dumping any errors into
        the assertion message so a broken resolver is easy to diagnose).
        """
        payload = {'query': query, 'variables': variables or {}}
        response = self.url_open(
            '/graphql/alokai',
            data=json.dumps(payload),
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(
            response.status_code, 200,
            "GraphQL endpoint should return HTTP 200, got %s: %s"
            % (response.status_code, response.text[:2000]),
        )
        body = response.json()
        if expect_errors:
            self.assertIn('errors', body, "Expected GraphQL errors but got: %s" % body)
        else:
            self.assertNotIn(
                'errors', body,
                "GraphQL returned errors:\n%s"
                % json.dumps(body.get('errors'), indent=2),
            )
            self.assertIn('data', body)
        return body

    def _login(self):
        """Authenticate the shared portal user for the current HTTP session."""
        self.authenticate(self.user.login, self.password)
