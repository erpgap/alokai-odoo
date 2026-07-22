# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Per-request memoisation for GraphQL resolvers.

Every resolver in a single GraphQL request shares ``info.context`` — a plain
dict built once per request by the controller (``{"env": request.env}``). Two
kinds of work are wasteful without a cache:

* values that are identical for the whole request (a shopper's wishlist), and
* an expensive per-record computation repeated across sibling field resolvers
  (a product's pricelist/tax combination info, recomputed once per price field).

Both are computed once here and reused. The cache lives only for the duration
of the request, so there is nothing to invalidate.
"""

CACHE_KEY = '_alokai_request_cache'


def _cache(info):
    return info.context.setdefault(CACHE_KEY, {})


def current_website(info):
    """Return the current website, resolved once per request.

    ``get_current_website()`` is called from several resolvers, some of which
    run once per product in a listing, yet the answer is the same for the whole
    request.
    """
    cache = _cache(info)
    if 'website' not in cache:
        cache['website'] = info.context['env']['website'].get_current_website()
    return cache['website']


def first_variant_with_image(info, product):
    """Return (cached) the product's first variant carrying a variant-specific
    image, or ``None`` — used by the image / imageUrl / thumbnail resolvers.

    Those three sibling resolvers each ask the same question, so a product-grid
    card would compute it three times per product; here it is computed once per
    product per request.
    """
    if not product:
        return None
    cache = _cache(info).setdefault('first_variant_image', {})
    key = (product._name, product.id)
    if key not in cache:
        variant = None
        if product._name == 'product.template':
            first = product.product_variant_ids[:1]
            if first and first.image_variant_1920:
                variant = first
        cache[key] = variant
    return cache[key]


def get_pricing_info(info, product):
    """Return the (cached) pricelist/tax combination info for ``product``.

    Selecting several price fields on a product (``variantPrice``,
    ``variantPriceAfterDiscount``, ``variantHasDiscountedPrice``,
    ``combinationInfo``…) would otherwise recompute this once per field. Here it
    is computed once per product per request.

    The raw dict is returned; callers that mutate it for serialisation must copy
    it first (``dict(...)``) so the shared cache entry stays intact.
    """
    if not product:
        return None
    cache = _cache(info).setdefault('pricing', {})
    key = (product._name, product.id)
    if key not in cache:
        if product._name == 'product.template':
            cache[key] = product._get_combination_info() or None
        else:
            cache[key] = product._get_combination_info_variant() or None
    return cache[key]


_REDIS_STOCK_TABLE = {
    'product.template': 'product_template_redis_stock',
    'product.product': 'product_product_redis_stock',
}


def redis_stock_qty(info, product):
    """Return the (cached) Redis stock quantity for ``product``, scoped to the
    current website.

    The whole website's redis-stock table is read once per request (per model),
    replacing a live ``free_qty`` aggregation run once per product. Consistent
    with the ``has_stock`` field the storefront already reads.
    """
    if not product:
        return 0.0
    table = _REDIS_STOCK_TABLE.get(product._name)
    if not table:
        return 0.0
    bucket = _cache(info).setdefault('redis_stock_qty', {})
    if table not in bucket:
        env = info.context['env']
        website = current_website(info)
        # ``table`` is a fixed value from the whitelist above, not user input.
        env.cr.execute(
            "SELECT product_id, quantity FROM {} WHERE website_id = %s".format(table),
            (website.id,))
        bucket[table] = dict(env.cr.fetchall())
    return bucket[table].get(product.id, 0.0)


def rating_stats(info, product):
    """Return the (cached) ``(count, avg)`` customer-rating stats for a
    product's template.

    ``rating_avg`` is restricted to internal users, so it must be read sudo;
    ``rating_count`` and ``rating_avg`` are computed together by Odoo. Read both
    from one sudo recordset and cache per template, instead of reading the count
    in the caller's environment and the average in a fresh sudo environment
    (which computed the same stats twice and defeated prefetch batching).
    """
    if not product:
        return (0, 0.0)
    tmpl = product.product_tmpl_id if product._name == 'product.product' else product
    bucket = _cache(info).setdefault('rating', {})
    if tmpl.id not in bucket:
        record = tmpl.sudo()
        count = record.rating_count or 0
        avg = round(record.rating_avg, 2) if count else 0.0
        bucket[tmpl.id] = (count, avg)
    return bucket[tmpl.id]


def is_in_wishlist(info, product):
    """Whether ``product`` (a template or a variant) is in the current wishlist.

    ``product.wishlist.current()`` is a search plus a publish/add-to-cart filter;
    calling it per product is an N+1. Load it once per request and cache the
    template/variant id-sets, turning each check into an O(1) membership test.
    """
    if not product:
        return False
    cache = _cache(info)
    if 'wishlist_ids' not in cache:
        wishlist = info.context['env']['product.wishlist'].current()
        cache['wishlist_ids'] = {
            'product.template': set(wishlist.mapped('product_id.product_tmpl_id').ids),
            'product.product': set(wishlist.mapped('product_id').ids),
        }
    return product.id in cache['wishlist_ids'].get(product._name, ())
