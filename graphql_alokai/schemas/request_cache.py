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

from odoo.tools.constants import PREFETCH_MAX

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
        if product._name != 'product.template':
            cache[key] = None
            return None
        # Answer for the whole listing page at once: one read of the variants
        # and one of their image attachments. ``bin_size`` only reads the
        # attachment size instead of loading every image from the filestore.
        templates = _siblings(info, product, 'first_variant_image_loaded')
        firsts = {template.id: template.product_variant_ids[:1] for template in templates}
        variants = product.env['product.product'].browse(
            first.id for first in firsts.values() if first)
        with_image = set(variants.with_context(bin_size=True).filtered('image_variant_1920').ids)
        # Iterated records keep the page-wide prefetch set, so the image
        # filename's name/write_date are then read in one query too.
        by_id = {variant.id: variant for variant in variants if variant.id in with_image}
        for template_id, first in firsts.items():
            cache[(product._name, template_id)] = by_id.get(first.id)
    return cache[key]


def _siblings(info, product, bucket):
    """The not-yet-handled records of ``product``'s prefetch batch (the
    product listing page it was loaded with), ``product`` included.

    ``bucket`` names the per-request set remembering which ids were already
    handled, so each batch is processed once whatever record asks first.
    """
    done = _cache(info).setdefault(bucket, {}).setdefault(product._name, set())
    ids = [product.id] + [
        id_ for id_ in product._prefetch_ids
        if id_ != product.id and isinstance(id_, int)
    ]
    ids = [id_ for id_ in ids if id_ not in done][:PREFETCH_MAX]
    done.update(ids)
    return product.browse(ids)


def _prefetch_pricing(info, product):
    """Load, for the whole listing page at once, the records the per-product
    ``_get_combination_info`` call reads.

    That call re-browses the variant by id, which drops the prefetch batch, so
    without this every product on the page would read its own variant, taxes
    and attribute values (about eight queries per product). Once loaded here
    those reads are served from the ORM cache.
    """
    records = _siblings(info, product, 'pricing_prefetched')
    if records._name == 'product.template':
        done = _cache(info)['pricing_prefetched'].setdefault('product.product', set())
        variants = records.product_variant_id.filtered(lambda variant: variant.id not in done)
        done.update(variants.ids)
    else:
        variants = records
    variants = variants.browse(variants.ids)
    if not variants:
        return
    # Reading one plain column loads every prefetchable column of the batch.
    variants.mapped('default_code')
    templates = variants.product_tmpl_id
    templates.mapped('taxes_id')
    variants.mapped('currency_id')
    ptavs = (
        variants.product_template_attribute_value_ids
        | templates.attribute_line_ids.product_template_value_ids
    )
    ptavs.mapped('ptav_active')
    ptavs.mapped('exclude_for')


def get_template_pricing_info(info, template):
    """Return the (cached) pricing info of ``template``'s first variant.

    The page is loaded from the template: unlike the computed
    ``product_variant_id``, it always carries the listing page's prefetch set.
    """
    if not template:
        return None
    _prefetch_pricing(info, template)
    return get_pricing_info(info, template.product_variant_id)


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
        _prefetch_pricing(info, product)
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
