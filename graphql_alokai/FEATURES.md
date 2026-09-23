# Alokai for Odoo — Feature Guide

A single **GraphQL API** (`/graphql/alokai`) that turns Odoo into a headless
eCommerce backend for the Alokai (Vue Storefront) frontend. One endpoint powers
the whole storefront: catalog, cart, checkout, account, content and SEO.

> Explore it live in the built-in GraphiQL IDE at `/graphiql/alokai`.

---

## 🛍️ Catalog & Product Discovery

- **Product listing** with pagination, and sorting by **name, price, newest, or
  popularity**.
- **Faceted search & filtering** — by category, attributes (color, size, …),
  **price range**, **in-stock only**, free-text search, or explicit IDs.
- **Live facet counts** — each attribute value and the in-stock filter come back
  with the number of matching products, plus the current **min/max price**, so
  the storefront can render an accurate filter sidebar.
- **Single product lookup** by **ID, slug, or barcode**.
- **Configurable products & variants** — full combination info, per-combination
  pricing, attribute matrices, and "is this combination available?" checks.
- **Rich media** — image galleries and videos, with **CDN-friendly templated
  image URLs** (`/web/image/.../{width}x{height}/...`) the frontend resizes on
  demand, plus an image-size guard to stop abusive resize requests.
- **Stock & availability** — real quantities, in-stock flags, out-of-stock
  messaging, and "allow out-of-stock ordering" per product.
- **Customer reviews** — average rating and review count (variants inherit their
  template's rating).

## 🤝 Merchandising & Recommendations

- **Frequently Bought Together (FBT)** — per product *and* for the whole cart.
- **Alternative products** ("you might also like").
- **Accessory products** ("goes well with").
- **Popularity-based sorting** driven by real sales data.

## 🗂️ Categories & Navigation

- **Category tree** with parents/children and top-level category listing.
- Lookup by **ID or slug** for clean, SEO-friendly URLs.
- Category images, banners, and breadcrumbs.
- **Website menus** — header menu, **mega-menu** (with images), and footer,
  multi-level and ready to render.

## 🛒 Cart & Checkout

- **Full cart** with line items, quantities, taxes, and live totals.
- **Batch cart operations** — add, update, and remove **multiple items at once**.
- **Clear cart** in one call.
- **Guest checkout** — create/update a customer without forcing registration.
- **Shipping** — list delivery methods with live rates and set one on the cart.
- **Coupons & promo codes**, **gift cards**, and **gift-card-only payment** for
  fully-covered orders.
- **Transparent totals** — subtotal, taxes, delivery, discounts, and gift-card
  amounts broken out separately.
- **Seamless handoff to Odoo checkout** via a secure one-time session token.

## ❤️ Wishlist

- Add and remove items, and list the current wishlist.
- Wishlist is **claimed and merged on login**, so guests don't lose it.

## 👤 Accounts & Authentication

- **Register, log in, log out.**
- **Password flows** — reset by email, change via token, and update while
  logged in.
- **Two-factor authentication (TOTP)** with **trusted-device** support.
- **My Account** — view and update profile, and **delete account (GDPR)**.
- **Newsletter opt-in** during signup or login.

## 📍 Addresses

- List billing and shipping addresses.
- **Add, update, delete (archived, never lost), and select** billing/shipping
  addresses — with automatic fiscal-position recalculation on the cart.
- **Country and state** lookups for address forms.

## 📦 Orders & Invoices

- **Order history** with filtering by status (quotation, sale, done, …) and
  invoice status, plus pagination and sorting.
- **Full order detail** — lines, taxes, totals, applied coupons/gift cards,
  delivery, payment transactions, and a portal link.
- **Invoices** — list and detail, amounts due, payment transactions, and a
  portal/PDF link.
- **Strictly access-controlled** — customers only ever see their own documents.

## 💳 Payments

- **Payment providers & methods** (with logos and card brands) filtered by
  country, company, and website.
- **Payment transactions** with live status, and a **payment-confirmation**
  endpoint for the post-payment "thank you" page.

## 📰 Content & SEO

- **Blog** — posts and tags, lookup by ID or slug, filtering by tag.
- **CMS pages** — static and product landing pages with slug routing.
- **Homepage** metadata and structured data.
- **Company profile** — contact details, logo, and social links.
- **SEO everywhere** — meta title/keywords/description/image **plus JSON-LD
  structured data** (products, categories, blog posts, homepage) and
  **breadcrumb JSON-LD**, so storefront pages are rich-result ready out of the
  box.

## ✉️ Leads & Marketing

- **Contact-us** form that creates a CRM lead — **with file attachments**.
- **Newsletter subscription** and per-list opt-in/opt-out management.
- **Loyalty** — coupons, promotions, and gift cards applied right on the cart.

## 🌍 Multi-store & Internationalization

- **Multi-website** — requests are routed to the right storefront by host.
- **Multi-currency** and **pricelist-aware** pricing (guest and customer).
- Country/state reference data for localized forms.

## ⚡ Built for Headless Performance

- **Redis-backed stock cache** kept in sync by lightweight background jobs.
- **On-demand cache invalidation** when records change.
- **CDN-friendly image URLs** so every size variant caches cleanly.
- **Redis slug sync** providing a dynamic-route fallback for records created
  after a storefront build.

---

## Try it

A product listing with facets, recommendations and SEO data in one request:

```graphql
{
  products(
    filter: { categorySlug: "/women", inStock: true }
    sort: { price: ASC }
    pageSize: 12
  ) {
    totalCount
    minPrice
    maxPrice
    attributeValues { id name }     # facet values
    filterCounts                    # counts per facet
    products {
      name
      slug
      price
      ratingAvg
      ratingCount
      imageUrl                      # templated, CDN-friendly
      frequentlyBoughtTogether { name slug price }
      jsonLd                        # structured data for SEO
    }
  }
}
```

Add several items to the cart in one round-trip:

```graphql
mutation {
  cartAddMultipleItems(products: [
    { id: 42, quantity: 2 },
    { id: 57, quantity: 1 }
  ]) {
    order { amountTotal amountTax cartQuantity }
    frequentlyBoughtTogether { name slug }
  }
}
```

Save something for later:

```graphql
mutation {
  wishlistAddItem(productId: 42) {
    totalCount
    wishlistItems { product { name slug } }
  }
}
```
