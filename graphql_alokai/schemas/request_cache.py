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
