# Alokai GraphQL API Reference

Developer reference for the Alokai GraphQL API. For a feature-level overview see
[FEATURES.md](FEATURES.md).

- **Endpoint:** `POST /graphql/alokai` (alias `/graphql/vsf`)
- **IDE:** `GET /graphiql/alokai` (internal users, or any user in debug mode)
- **Auth:** session-cookie based. Operations marked **🔒 auth** require a
  logged-in (portal) user; everything else is available to the public website
  user. Log in with the `login` mutation.
- **Naming:** all fields, arguments and input keys are **camelCase**
  (e.g. `currentPage`, `categorySlug`, `subscribeNewsletter`).
- **Host routing:** send the storefront host in the `HTTP_REQUEST_HOST` header
  so the request is routed to the correct website (multi-store).

List endpoints share a common shape: `filter`, `currentPage` (1-based),
`pageSize`, `search`, `sort`, and return a `totalCount` alongside the records.

---

## Queries

### Catalog

| Query | Arguments | Returns |
|-------|-----------|---------|
| `products` | `filter: ProductFilterInput`, `currentPage: Int = 1`, `pageSize: Int = 20`, `search: String`, `sort: ProductSortInput` | `Products` |
| `product` | `id: Int`, `slug: String`, `barcode: String` | `Product` |
| `attribute` | `id: Int!` | `Attribute!` |
| `productVariant` | `productTemplateId: Int`, `combinationId: [Int]` | `ProductVariant!` |

`Products` returns: `products [Product]`, `totalCount Int!`,
`attributeValues [AttributeValue]` (facets), `minPrice Float`, `maxPrice Float`,
`filterCounts` (per-facet counts), `searchUrl String`.

### Categories

| Query | Arguments | Returns |
|-------|-----------|---------|
| `categories` | `filter: CategoryFilterInput`, `currentPage`, `pageSize`, `search`, `sort: CategorySortInput` | `Categories` |
| `category` | `id: Int`, `slug: String` | `Category` |

### Countries

| Query | Arguments | Returns |
|-------|-----------|---------|
| `countries` | `filter: CountryFilterInput`, `currentPage`, `pageSize`, `search`, `sort: CountrySortInput` | `Countries` |
| `country` | `id: Int`, `code: String` | `Country!` |

### Cart

| Query | Arguments | Returns |
|-------|-----------|---------|
| `cart` | – | `Cart` (`order Order`, `frequentlyBoughtTogether [Product]`) |

### Orders 🔒

| Query | Arguments | Returns |
|-------|-----------|---------|
| `orders` | `filter: OrderFilterInput`, `currentPage`, `pageSize: Int = 10`, `sort: OrderSortInput` | `Orders` |
| `order` | `id: Int!` | `Order!` |
| `deliveryMethods` | – | `[ShippingMethod!]` |

### Invoices 🔒

| Query | Arguments | Returns |
|-------|-----------|---------|
| `invoices` | `currentPage`, `pageSize: Int = 10`, `sort: InvoiceSortInput` | `Invoices` |
| `invoice` | `id: Int!` | `Invoice!` |

### Account & Addresses

| Query | Arguments | Returns |
|-------|-----------|---------|
| `partner` 🔒 | – | `Partner!` |
| `addresses` | `filter: AddressFilterInput` | `[Partner!]` |

### Payments

| Query | Arguments | Returns |
|-------|-----------|---------|
| `paymentProvider` | `id: Int!` | `PaymentProvider!` |
| `paymentProviders` | – (uses current cart) | `[PaymentProvider!]` |
| `paymentTransaction` | `id: Int`, `reference: String` | `PaymentTransaction!` |
| `paymentConfirmation` | – (uses session) | `Cart` |

### Wishlist

| Query | Arguments | Returns |
|-------|-----------|---------|
| `wishlistItems` | – | `WishlistData` (`wishlistItems [WishlistItem]`, `totalCount`) |

### Mailing

| Query | Arguments | Returns |
|-------|-----------|---------|
| `mailingContacts` 🔒 | `filter`, `currentPage`, `pageSize`, `search`, `sort` | `MailingContacts` |
| `mailingList` | `id: Int!` | `MailingList!` |
| `mailingLists` | `filter`, `currentPage`, `pageSize`, `search`, `sort` | `MailingLists` |

### Website & Content

| Query | Arguments | Returns |
|-------|-----------|---------|
| `websiteMenu` | `noParent: Boolean` | `[WebsiteMenu!]` |
| `websiteMegaMenu` | `noParent: Boolean` | `[WebsiteMenu!]` |
| `websiteFooter` | `noParent: Boolean` | `[WebsiteMenu!]` |
| `websiteHomepage` | – | `Homepage` |
| `blogTags` | – | `BlogTags` |
| `blogPost` | `id: Int`, `slug: String` | `BlogPost!` |
| `blogPosts` | `filter: BlogPostFilterInput`, `currentPage`, `pageSize: Int = 10`, `search`, `sort` | `BlogPosts` |
| `websitePage` | `id: Int`, `pageSlug: String` | `WebsitePage` |
| `websitePages` | `filter: WebsitePageFilterInput`, `currentPage`, `pageSize`, `search`, `sort` | `WebsitePages` |

---

## Mutations

### Authentication

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `login` | `email: String!`, `password: String!`, `subscribeNewsletter: Boolean = false` | `LoginOutput` (`user`, `cart`, `wishlistItems`) |
| `logout` | – | `Boolean` |
| `register` | `name: String!`, `email: String!`, `password: String!`, `subscribeNewsletter: Boolean = false` | `User` |
| `resetPassword` | `email: String!` | `User` |
| `changePassword` | `token: String!`, `newPassword: String!` | `User` |
| `updatePassword` 🔒 | `currentPassword: String!`, `newPassword: String!` | `User` |
| `totpVerification` | `code: String!`, `userId: Int!`, `rememberDevice: Boolean = false` | `TwoFactorOutput` |
| `checkoutRedirect` | `sessionId: String!` | `CheckoutRedirectOutput` (`accessToken`) |

### Account

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `updateMyAccount` 🔒 | `myaccount: UpdateMyAccountParams` | `Partner` |
| `deleteMyAccount` 🔒 | – | `Boolean` |

### Addresses

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `addAddress` | `type: AddressEnum!`, `address: AddAddressInput` | `Partner` |
| `updateAddress` 🔒 | `address: UpdateAddressInput!` | `Partner` |
| `deleteAddress` | `address: DeleteAddressInput` | `{ result: Boolean }` |
| `selectAddress` | `type: AddressEnum!`, `address: SelectAddressInput` | `Partner` |

### Cart & Checkout

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `cartAddMultipleItems` | `products: [ProductInput!]!` | `CartData` |
| `cartUpdateMultipleItems` | `lines: [CartLineInput!]!` | `CartData` |
| `cartRemoveMultipleItems` | `lineIds: [Int]!` | `CartData` |
| `cartClear` | – | `Order` |
| `setShippingMethod` | `shippingMethodId: Int!` | `CartData` |
| `createUpdatePartner` | `name: String!`, `email: String!`, `subscribeNewsletter: Boolean!`, `phone: String`, `mobile: String` | `Partner` |
| `applyCoupon` | `promo: String` | `ApplyCouponList` (`order`, `error`) |
| `applyGiftCard` | `promo: String` | `ApplyGiftCardList` (`order`, `error`) |
| `makeGiftCardPayment` | – | `{ done: Boolean }` |

### Wishlist

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `wishlistAddItem` | `productId: Int!` | `WishlistData` |
| `wishlistRemoveItem` | `wishId: Int!` | `WishlistData` |

### Leads & Mailing

| Mutation | Arguments | Returns |
|----------|-----------|---------|
| `contactUs` | `contactus: ContactUsParams` | `Lead` |
| `newsletterSubscribe` | `email: String` | `{ subscribed: Boolean }` |
| `userAddMultipleMailing` 🔒 | `mailings: [MailingInput]!` | `MailingContact` |

---

## Input objects

```graphql
input ProductFilterInput {
  ids: [Int]
  categoryId: [Int]
  categorySlug: String
  attributeValueId: [Int]   # deprecated, use attribValues
  attribValues: [String]    # "attributeId-valueId", e.g. "3-12"
  name: String
  minPrice: Float
  maxPrice: Float
  inStock: Boolean
}
input ProductSortInput { id: SortEnum  name: SortEnum  price: SortEnum  popular: SortEnum  newest: SortEnum }

input CategoryFilterInput { id: [Int]  parent: Boolean }   # parent: true = top-level only
input CategorySortInput { id: SortEnum }

input CountryFilterInput { id: Int }
input CountrySortInput { id: SortEnum }

input OrderFilterInput { stages: [OrderStage]  invoiceStatus: [InvoiceStatus] }
input OrderSortInput { id: SortEnum  dateOrder: SortEnum  name: SortEnum  state: SortEnum }
input InvoiceSortInput { id: SortEnum  invoiceDate: SortEnum  name: SortEnum  state: SortEnum }

input AddressFilterInput { addressType: [AddressEnum] }

input BlogPostFilterInput { tagId: [Int]  tagSlug: String }
input BlogPostSortInput { id: SortEnum  publishedDate: SortEnum  name: SortEnum }

input WebsitePageFilterInput { id: [Int]  pageSlug: String  pageType: [PageTypeEnum] }

input ContactUsParams {
  name: String!  email: String!  phone: String!
  company: String  subject: String!  message: String!
  attachments: [ContactusAttachmentInput]
}
input ContactusAttachmentInput { name: String!  fileData: String! }   # fileData = base64

input UpdateMyAccountParams { id: Int  name: String  email: String  phone: String }

input AddAddressInput {
  name: String!  street: String!  street2: String  zip: String!
  city: String  stateId: Int  countryId: Int!  phone: String!  email: String
}
input UpdateAddressInput {
  id: Int!  name: String  street: String  street2: String  zip: String
  city: String  stateId: Int  countryId: Int  phone: String  email: String
}
input DeleteAddressInput { id: Int! }
input SelectAddressInput { id: Int! }

input ProductInput  { id: Int!  quantity: Int! }
input CartLineInput { id: Int!  quantity: Int! }
input MailingInput  { mailinglistId: Int!  optout: Boolean! }
```

## Enums

```graphql
enum SortEnum { ASC  DESC }
enum AddressEnum { Billing  Shipping }            # mutation arg
enum AddressType { Contact  InvoiceAddress  DeliveryAddress  OtherAddress  PrivateAddress }
enum OrderStage { Quotation  QuotationSent  SalesOrder  Locked  Cancelled }
enum InvoiceStatus { UpsellingOpportunity  FullyInvoiced  ToInvoice  NothingtoInvoice }
enum InvoiceState { Draft  Posted  Cancelled }
enum PaymentTransactionState { Draft  Pending  Authorized  Confirmed  Canceled  Error }
enum VariantCreateMode { Instantly  Dynamically  NeverOption }
enum FilterVisibility { Visible  Hidden }
enum PageTypeEnum { STATIC  PRODUCTS }
```

---

## Key object types

> Relations point to the type in **bold**. Image fields come in three flavours:
> `image` (plain URL), `imageFilename`, and `imageUrl` (a **templated**
> `/web/image/.../{width}x{height}/...` URL the frontend fills in per size).
> Generic JSON fields (`combinationInfo`, `jsonLd`, `breadcrumb`, `taxTotals`,
> `filterCounts`) are returned without a sub-selection.

**Product** — `id name displayName sku barcode description websiteDescription
typeId visibility status weight price qty slug` · pricing
(`variantPrice variantPriceAfterDiscount variantHasDiscountedPrice
combinationInfo combinationInfoVariant`) · stock (`isInStock allowOutOfStock
showAvailableQty outOfStockMessage`) · media (`image smallImage thumbnail
imageUrl mediaGallery[ProductImage]`) · SEO (`metaTitle metaKeyword
metaDescription metaImage jsonLd breadcrumb jsonLdBreadcrumb`) · reviews
(`ratingCount ratingAvg`) · relations (`currency`**Currency**,
`categories[`**Category**`] ribbon`**Ribbon**`, tags[`**ProductTag**`],
attributeValues/variantAttributeValues[`**AttributeValue**`],
productVariants/firstVariant/productTemplate`**Product**`,
isInWishlist isVariantPossible`) · merchandising (`alternativeProducts
accessoryProducts frequentlyBoughtTogether[`**Product**`]`) ·
`alokaiPages[`**WebsitePage**`]`.

**Category** — `id name slug image imageUrl parent`**Category**`
childs[`**Category**`] metaTitle metaKeyword
metaDescription metaImage jsonLd breadcrumb`.

**Attribute** — `id name displayType variantCreateMode filterVisibility
values[`**AttributeValue**`]`.
**AttributeValue** — `id name displayType htmlColor search priceExtra
attribute`**Attribute**.

**Order** — `id name dateOrder stage clientOrderRef orderUrl cartQuantity
invoiceStatus invoiceCount` · amounts (`amountUntaxed amountTax amountTotal
amountDelivery amountSubtotal amountDiscounts amountGiftCards taxTotals
currencyRate`) · relations (`partner/partnerShipping/partnerInvoice`**Partner**`,
currency`**Currency**`, shippingMethod`**ShippingMethod**`,
orderLines/websiteOrderLine/reportOrderLine[`**OrderLine**`],
transactions/lastTransaction[`**PaymentTransaction**`],
coupons[`**Coupon**`] giftCards[`**GiftCard**`]`).
**OrderLine** — `id name quantity priceUnit priceSubtotal priceTotal priceTax
shopWarning product`**Product**` giftCard`**GiftCard**` coupon`**Coupon**.

**Invoice** — `id name invoiceDate invoiceDateDue state invoiceUrl amountUntaxed
amountTax amountTotal amountResidual taxTotals partner`**Partner**`
currency`**Currency**` invoiceLines[`**InvoiceLine**`]
transactions[`**PaymentTransaction**`]`.

**Partner** — `id name email phone street street2 city zip vat isCompany
isPublic companyName companyRegNo addressType image imageUrl country`**Country**`
state`**State**` billingAddress/shippingAddress/company/parentId`**Partner**`
contacts[`**Partner**`] publicPricelist/currentPricelist`**Pricelist**``.

**User** — `id name email totpRequired partner`**Partner**.

**PaymentProvider** — `id name code paymentMethods[`**PaymentMethod**`]`.
**PaymentMethod** — `id name code active sequence image imagePaymentForm
imageUrl providers[`**PaymentProvider**`] brands[`**PaymentMethod**`]`.
**PaymentTransaction** — `id reference amount provider providerReference state
payment`**Payment**` currency`**Currency**` company/customer`**Partner**.

**WishlistItem** — `id partner`**Partner**` product`**Product**.

**ShippingMethod** — `id name price product`**Product**.

**WebsiteMenu** — `id name url isFooter isMegaMenu sequence
parent`**WebsiteMenu**` childs[`**WebsiteMenu**`]
images[`**WebsiteMenuImage**`]`.
**Homepage** — `metaTitle metaKeyword metaDescription metaImage
metaImageFilename jsonLd`.
**WebsitePage** — `id name pageType websiteUrl isPublished publishingDate
content website`**Website**``.

**BlogPost** — `id name slug image imageUrl publishedDate content teaser jsonLd
authorId`**Partner**` tags[`**BlogTag**`]`.
**BlogTag** — `id name slug`.

**Lead** — `id name email phone company subject message`.
**Country** — `id name code states[`**State**`]`. **State** — `id name code`.
**Currency** — `id name symbol`. **Pricelist** — `id name currency`**Currency**.

---

## Examples

**Authenticate** (the session cookie returned is reused by later requests):

```graphql
mutation {
  login(email: "jane@example.com", password: "••••••") {
    user { id name email }
    cart { id amountTotal }
    wishlistItems { product { name } }
  }
}
```

**Product detail page** by slug, with variants, recommendations and SEO:

```graphql
query ($slug: String) {
  product(slug: $slug) {
    name sku price imageUrl
    mediaGallery { imageUrl video }
    attributeValues { id name htmlColor }
    productVariants { id sku variantPrice }
    accessoryProducts { name slug }
    frequentlyBoughtTogether { name slug price }
    ratingAvg ratingCount
    jsonLd jsonLdBreadcrumb
  }
}
```

**Apply a coupon** (errors are returned in the `error` field, not as GraphQL errors):

```graphql
mutation { applyCoupon(promo: "SUMMER10") { order { amountTotal } error } }
```
