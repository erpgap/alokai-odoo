# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import base64
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta

from odoo import api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


def pre_init_hook_login_check(env):
    """
    This hook will see if exists any conflict between Portal logins, before the module is installed
    """
    check_users = []
    users = env['res.users'].search([])
    for user in users:
        if user.login and user.has_group('base.group_portal'):
            login = user.login.lower()
            if login not in check_users:
                check_users.append(login)
            else:
                raise ValidationError(
                    _("Conflicting user logins exist for `%s`", login)
                )


def _seed_cms_homepage(env):
    """Give a fresh install an editable homepage.

    Without this a merchant installs Alokai, opens the CMS, and finds no
    homepage to edit - the storefront still renders it, from markup they
    cannot reach. The point of the CMS is that they can.

    The blocks are a snapshot of the storefront's default homepage, taken at
    install time. They are not kept in step with later storefront releases on
    purpose: once this runs, the content belongs to the merchant, and a
    release that changes a block's shape is handled by the schema migrations
    on the storefront side rather than by overwriting what they wrote.

    Idempotent - an existing page for / is left exactly as it is.
    """
    path = os.path.join(os.path.dirname(__file__), 'data', 'cms_homepage_blocks.json')
    try:
        with open(path, encoding='utf-8') as handle:
            # {year} is the install year: the merchant owns the text from
            # here on, and edits it like any other copy.
            blocks = json.loads(
                handle.read().replace('{year}', str(datetime.now().year)))
    except (OSError, ValueError) as error:
        _logger.warning('Could not read the default homepage blocks: %s', error)
        return

    try:
        env['alokai.website.page'].seed_homepage(blocks, name='Homepage')
    except Exception as error:  # noqa: BLE001 - never block an install for this
        _logger.warning('Could not seed the CMS homepage: %s', error)


_DEMO_IMAGE = re.compile(r'^\{image:([\w.]+)\}$')


def _resolve_demo_images(env, value, attachment_ids):
    """Swap `{image:<xml id>}` for the URL the media library serves it at.

    Attachment ids differ per database, so the demo content names its images
    by XML id (data/demo_cms_images.xml) and this fills in the real ids.
    """
    if isinstance(value, dict):
        return {key: _resolve_demo_images(env, item, attachment_ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_demo_images(env, item, attachment_ids) for item in value]
    if isinstance(value, str):
        match = _DEMO_IMAGE.match(value)
        if match:
            attachment = env.ref(match.group(1))
            attachment_ids.append(attachment.id)
            return '/web/image/%s' % attachment.id
    return value


def _seed_cms_demo_pages(env):
    """The demo store's content pages - about, FAQ, shipping and the rest.

    They are demo content, not part of the storefront: a real store writes its
    own, so they exist only where demo data was installed. Each is a normal,
    published CMS page the merchant can edit or delete.

    An address that already has a page is skipped, so nothing a merchant
    wrote is ever overwritten.
    """
    path = os.path.join(os.path.dirname(__file__), 'data', 'cms_demo_pages.json')
    try:
        with open(path, encoding='utf-8') as handle:
            pages = json.load(handle)
    except (OSError, ValueError) as error:
        _logger.warning('Could not read the demo content pages: %s', error)
        return

    Page = env['alokai.website.page']
    website = env['website'].get_current_website()
    created = 0
    for spec in pages:
        if Page.search_count([
            ('url', '=', spec['url']),
            ('website_id', 'in', (False, website.id)),
        ]):
            continue
        attachment_ids = []
        page = Page.create({
            'name': spec['name'],
            'url': spec['url'],
            'website_id': website.id,
            'draft_blocks': _resolve_demo_images(env, spec['blocks'], attachment_ids),
        })
        # The references the editor would send on publish, so the images are
        # known to be in use rather than looking like orphans.
        page.publish_draft(references={'attachment_ids': attachment_ids})
        page.set_seo('en_US', {
            'title': spec.get('meta_title'),
            'description': spec.get('meta_description'),
        })
        created += 1
    _logger.info('Demo content pages created: %s', created)


def _seed_demo_homepage_seo(env):
    """Give the demo store's homepage its meta title and description.

    These used to sit on the website record, which made the homepage the one
    page whose tags were not its own. They are set here, on the page, for the
    same reason the other demo pages set theirs: it is demo copy, so a real
    store installing without demo data starts empty rather than branded as
    Alokai.

    Skipped when the homepage already has tags, so re-running an install never
    overwrites what a merchant wrote.
    """
    path = os.path.join(
        os.path.dirname(__file__), 'data', 'cms_demo_homepage_seo.json')
    try:
        with open(path, encoding='utf-8') as handle:
            seo = json.load(handle)
    except (OSError, ValueError) as error:
        _logger.warning('Could not read the demo homepage SEO: %s', error)
        return

    website = env['website'].get_current_website()
    home = env['alokai.website.page'].search([
        ('url', '=', '/'),
        ('is_system', '=', True),
        ('website_id', 'in', (False, website.id)),
    ], limit=1)
    if not home or home.website_meta_title:
        return

    home.set_seo('en_US', {
        'title': seo.get('meta_title'),
        'description': seo.get('meta_description'),
    })
    _logger.info('Demo homepage SEO set on page %s', home.id)


def _seed_cms_regions(env):
    """Declare the slots the storefront provides.

    These exist so a merchant can add content to category and product pages,
    whose structure is otherwise entirely owned by code. They start empty and
    render nothing, so installing this changes nothing a visitor sees.

    Adding a region later is a line here plus a <CmsRegion> in the template.
    """
    regions = [
        ('category-after', 'Below category pages'),
        ('product-after', 'Below product pages'),
    ]
    for key, name in regions:
        try:
            env['alokai.website.page'].seed_region(key, name)
        except Exception as error:  # noqa: BLE001 - never block an install
            _logger.warning('Could not seed the %s region: %s', key, error)


def post_init_hook_login_convert(env):
    """
    After the module is installed.

    Production tasks (always run):
      - Lowercase portal user logins
      - Compute hierarchical website_slug on graphql_alokai categories
      - Repair blog.post slugs accidentally storing 'False/' or 'None/' prefixes

    Demo tasks (only run when this module's demo data was loaded):
      - Load product images from image_manifest.json
      - Stock all variants with a default quantity
      - Generate fake customer reviews on every product
      - Generate demo sales history (popularity + frequently-bought-together)
      - Set alternative products (upsell) on each product
      - Create the demo store's content pages (about, FAQ, ...) in the CMS
      - Enable Redis and push slugs and stock, if a Redis server is reachable
    """
    # ---- Production tasks ------------------------------------------------
    users = env['res.users'].search([])
    for user in users:
        if user.login and user.has_group('base.group_portal'):
            user.login = user.login.lower()

    _compute_category_slugs(env)
    _fix_broken_blog_slugs(env)
    _seed_cms_homepage(env)
    _seed_cms_regions(env)

    # ---- Demo tasks (gated) ---------------------------------------------
    # Use the existence of a graphql_alokai-created product.template as a
    # proxy for "demo data was installed". When the module is installed
    # without demo data, none exist and the demo helpers are skipped entirely.
    demo_data_present = bool(env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.template'),
    ], limit=1))
    if not demo_data_present:
        return

    _load_demo_product_images(env)
    _stock_demo_variants(env)
    _add_demo_product_reviews(env)
    _generate_demo_sales(env)
    _generate_demo_alternatives(env)
    _clear_unwanted_social_fields(env)
    _seed_cms_demo_pages(env)
    _seed_demo_homepage_seo(env)
    # Last, so the bulk demo writes above run with Redis still disabled.
    env['website']._alokai_demo_enable_redis()


def _resolve_image_path(addon_dir, local_path):
    """Return the resolved absolute path for an image, trying .png fallback for .jpg paths."""
    path = os.path.join(addon_dir, local_path)
    if os.path.exists(path):
        return path
    # Try the other extension as fallback
    if local_path.endswith('.jpg'):
        alt = os.path.join(addon_dir, local_path[:-4] + '.png')
    elif local_path.endswith('.png'):
        alt = os.path.join(addon_dir, local_path[:-4] + '.jpg')
    else:
        return None
    return alt if os.path.exists(alt) else None


def _load_demo_product_images(env):
    """
    Read graphql_alokai/data/image_manifest.json and assign images to product
    templates and variants based on SKU + color attribute matching.

    Template image_1920: the first 'done' image of the product
    Variant image_variant_1920: matched by color attribute name
    """
    # Module dir: .../addons/alokai-odoo/graphql_alokai
    # Manifest:    .../addons/alokai-odoo/graphql_alokai/data/image_manifest.json
    # The manifest lives inside the module so it is always deployed with the
    # addon. Image local_path values are repo-relative (graphql_alokai/static/
    # ...), so they still resolve against addon_dir (the module's parent).
    module_dir = os.path.dirname(os.path.abspath(__file__))
    addon_dir = os.path.dirname(module_dir)
    manifest_path = os.path.join(module_dir, 'data', 'image_manifest.json')

    if not os.path.exists(manifest_path):
        _logger.info("No image_manifest.json found at %s; skipping image loading", manifest_path)
        return

    with open(manifest_path) as fp:
        manifest = json.load(fp)

    total_templates = 0
    total_variants = 0
    missing_files = 0

    for entry in manifest.get('products', []):
        done_images = [img for img in entry.get('images', []) if img.get('status') == 'done']
        if not done_images:
            continue

        # Look up by XML ID (default_code is NULL on templates with multiple variants)
        xmlid = f"graphql_alokai.{entry['template_id']}"
        try:
            template = env.ref(xmlid)
        except ValueError:
            _logger.warning("No product.template found for xmlid %s", xmlid)
            continue

        # Set template image from the first done image
        first_img_path = _resolve_image_path(addon_dir, done_images[0]['local_path'])
        if first_img_path:
            with open(first_img_path, 'rb') as f:
                template.image_1920 = base64.b64encode(f.read())
            total_templates += 1
        else:
            _logger.warning("Image file missing: %s", os.path.join(addon_dir, done_images[0]['local_path']))
            missing_files += 1

        # For variant products, assign per-color images then align the template
        # image with the first variant so the listing thumbnail is consistent
        # with the product page default (which auto-selects the first variant).
        if entry.get('has_variants'):
            for img in done_images:
                color_name = img.get('color')
                if not color_name:
                    continue
                img_path = _resolve_image_path(addon_dir, img['local_path'])
                if not img_path:
                    _logger.warning("Variant image missing: %s", os.path.join(addon_dir, img['local_path']))
                    missing_files += 1
                    continue

                # Find the variant whose Color attribute matches this color
                target_variant = None
                for variant in template.product_variant_ids:
                    for ptav in variant.product_template_variant_value_ids:
                        if (ptav.attribute_id.name == 'Color'
                                and ptav.product_attribute_value_id.name == color_name):
                            target_variant = variant
                            break
                    if target_variant:
                        break

                if target_variant:
                    with open(img_path, 'rb') as f:
                        target_variant.image_variant_1920 = base64.b64encode(f.read())
                    total_variants += 1
                else:
                    _logger.warning(
                        "No variant matched color %s for SKU %s", color_name, entry['sku_base']
                    )

            # Align template image with first variant so listing thumbnail
            # matches the product page default.
            first_variant = template.product_variant_ids[:1]
            if first_variant and first_variant.image_variant_1920:
                template.image_1920 = first_variant.image_variant_1920

    _logger.info(
        "Demo images loaded: %s templates, %s variants, %s missing files",
        total_templates, total_variants, missing_files,
    )


def _stock_demo_variants(env, quantity=100):
    """
    Add stock for every variant of every product.template loaded by graphql_alokai.

    Fast path: creates stock.quant rows directly with `quantity` (no inventory
    adjustment machinery). Same end state as inventory_quantity + apply, but
    bypasses stock.move.line creation, valuation hooks, etc.

    Idempotent: skips variants that already have a quant in the target location.
    """
    imd = env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.template'),
    ])
    template_ids = imd.mapped('res_id')
    if not template_ids:
        _logger.info("No graphql_alokai templates found; skipping stock setup")
        return

    templates = env['product.template'].browse(template_ids).exists()
    variants = templates.mapped('product_variant_ids').filtered(lambda v: v.is_storable)
    if not variants:
        _logger.info("No storable variants found; skipping stock setup")
        return

    warehouse = env['stock.warehouse'].search([('company_id', '=', env.company.id)], limit=1)
    if not warehouse:
        _logger.warning("No warehouse found for company %s; skipping stock setup", env.company.name)
        return
    stock_location = warehouse.lot_stock_id

    # Find variants that already have a quant in this location -> skip them
    existing_pairs = env['stock.quant'].search_read(
        [('product_id', 'in', variants.ids), ('location_id', '=', stock_location.id)],
        ['product_id'],
    )
    existing_product_ids = {row['product_id'][0] for row in existing_pairs}

    vals_list = [
        {
            'product_id': variant.id,
            'location_id': stock_location.id,
            'quantity': quantity,
        }
        for variant in variants
        if variant.id not in existing_product_ids
    ]
    if not vals_list:
        _logger.info("All %s variants already have stock; nothing to do", len(variants))
        return

    env['stock.quant'].sudo().create(vals_list)
    _logger.info(
        "Stock setup complete: %s variants stocked (qty=%s), %s already had stock",
        len(vals_list), quantity, len(variants) - len(vals_list),
    )


def _compute_category_slugs(env):
    """
    Set hierarchical website_slug values on product.public.category records
    created by this module (whether shipped as data or demo data).

    Default behaviour of product.public.category.create() falls back to
    `/category/{id}` when no slug is given - replace those with a path-style
    slug walking up the parent chain, e.g.:
        /women
        /women/clothing
        /women/clothing/dresses
        /men/accessories/wallets

    Hierarchical paths are unique by construction (different parents -> different
    paths) and SEO-friendly. The model's _validate_website_slug guards against
    accidental collisions.

    Idempotent: only updates categories whose current slug still matches the
    default `/category/{id}` pattern. Safe to run in production - no-op if this
    module has not created any categories.
    """
    imd = env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.public.category'),
    ])
    if not imd:
        _logger.info("No graphql_alokai categories found; skipping slug computation")
        return

    categories = env['product.public.category'].browse(imd.mapped('res_id')).exists()
    slugify = env['ir.http']._slugify
    langs = env['res.lang'].search([])

    def hierarchical_slug(cat):
        """Walk up parent chain and build '/parent/.../leaf' slug."""
        parts = []
        node = cat
        while node:
            slug_part = slugify(node.name or '').strip().strip('-')
            if slug_part:
                parts.append(slug_part)
            node = node.parent_id
        parts.reverse()
        return '/' + '/'.join(parts) if parts else None

    # Sort parents-first so any cross-checks see consistent state
    sorted_cats = categories.sorted(lambda c: (len(c.parent_path or ''), c.id))

    updated = 0
    for category in sorted_cats:
        default_slug = f'/category/{category.id}'
        # Skip if user/admin already set a custom slug
        if category.website_slug and category.website_slug != default_slug:
            continue
        for lang in langs:
            cat = category.with_context(lang=lang.code)
            slug = hierarchical_slug(cat)
            if slug:
                cat.website_slug = slug
        updated += 1

    _logger.info("Category slugs computed: %s categories updated", updated)


# Sample customer review feedback - varied so reviews don't look templated
REVIEW_FEEDBACKS = [
    "Excellent quality, exactly as described. Very happy with my purchase!",
    "Beautiful product, great craftsmanship. Highly recommend.",
    "Fast shipping and great value for money.",
    "Love it! The fit is perfect and the material feels premium.",
    "Good product overall, would buy again.",
    "Stylish and well-made. Gets compliments every time I wear it.",
    "Met my expectations. Solid quality at a fair price.",
    "Comfortable and looks great. Will definitely shop here again.",
    "Excellent customer service and a great product to match.",
    "Looks even better in person than in the photos.",
    "Worth every penny. Great attention to detail.",
    "Really pleased with this purchase. The colors are vivid and accurate.",
    "Fits true to size. Good purchase.",
    "Beautiful design and excellent quality. Recommended.",
    "Arrived quickly and well-packaged. Five stars!",
]

# Sample customer first names for fake review authors (mix of common names)
REVIEW_NAMES = [
    "Sarah Mitchell", "James Carter", "Emma Davis", "Michael Lee", "Olivia Brown",
    "David Wilson", "Sophia Martinez", "Daniel Thompson", "Isabella Anderson",
    "Christopher Taylor", "Ava Johnson", "Matthew White", "Mia Harris",
    "Andrew Robinson", "Charlotte Walker", "Joseph Clark", "Amelia King",
    "Ryan Wright", "Emily Scott", "Benjamin Hill",
]


def _add_demo_product_reviews(env, min_reviews=3, max_reviews=8):
    """
    Generate fake customer reviews (rating.rating) for every product.template
    created by this module. Ratings are randomized between 3 and 5 stars.

    Idempotent: skips templates that already have reviews.
    """
    imd = env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.template'),
    ])
    if not imd:
        _logger.info("No graphql_alokai templates found; skipping reviews")
        return

    templates = env['product.template'].browse(imd.mapped('res_id')).exists()
    if not templates:
        return

    # Find the ir.model record for product.template (required by rating.rating)
    res_model = env['ir.model'].search([('model', '=', 'product.template')], limit=1)
    if not res_model:
        _logger.warning("ir.model for product.template not found; skipping reviews")
        return

    # Find or create demo partners for review authors
    partners = []
    for name in REVIEW_NAMES:
        partner = env['res.partner'].search([('name', '=', name)], limit=1)
        if not partner:
            partner = env['res.partner'].create({
                'name': name,
                'company_type': 'person',
                'customer_rank': 1,
            })
        partners.append(partner)

    Rating = env['rating.rating']
    # Seed for reproducibility - same install -> same review distribution
    rng = random.Random(42)
    created_count = 0
    skipped_count = 0
    now = datetime.now()

    for template in templates:
        # Skip if this template already has reviews
        existing = Rating.search_count([
            ('res_model', '=', 'product.template'),
            ('res_id', '=', template.id),
        ])
        if existing:
            skipped_count += 1
            continue

        n_reviews = rng.randint(min_reviews, max_reviews)
        vals_list = []
        for _ in range(n_reviews):
            # Bias toward higher ratings: weighted random 3-5 (more 4s and 5s)
            rating_value = rng.choices([3, 4, 5], weights=[1, 3, 4])[0]
            vals_list.append({
                'res_model_id': res_model.id,
                'res_id': template.id,
                'partner_id': rng.choice(partners).id,
                'rating': float(rating_value),
                'feedback': rng.choice(REVIEW_FEEDBACKS),
                'consumed': True,
                'rated_on': now - timedelta(days=rng.randint(1, 180)),
            })
        Rating.sudo().create(vals_list)
        created_count += len(vals_list)

    _logger.info(
        "Demo reviews added: %s ratings across %s templates (%s already had reviews)",
        created_count, len(templates) - skipped_count, skipped_count,
    )


def _generate_demo_sales(env, n_orders=3000, lookback_days=3650):
    """Generate believable demo sales history so the popularity and
    frequently-bought-together crons compute real, stable data.

    Both metrics derive from sale.report (confirmed orders) and only look back
    `alokai_recent_sales_count_days`. We:
      - widen that window so the static demo orders keep counting (otherwise the
        data would fade ~30 days after install),
      - create ~n_orders orders in state 'sale' (no action_confirm, so no stock
        pickings) with category-themed baskets and weighted product picks (a few
        clear bestsellers -> long-tail popularity, repeated co-occurrence ->
        meaningful FBT), dated over the past ~6 months,
      - run both crons so popularity + FBT are populated immediately.

    Deterministic (fixed seed); idempotent (skips if demo orders already exist).
    """
    SaleOrder = env['sale.order']
    if SaleOrder.search_count([('client_order_ref', '=', 'ALOKAI_DEMO')]):
        _logger.info("Demo sales already generated; skipping")
        return

    imd = env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.template'),
    ])
    templates = env['product.template'].browse(imd.mapped('res_id')).exists()
    templates = templates.filtered(
        lambda t: t.sale_ok and t.product_variant_ids
    ).sorted('id')
    if len(templates) < 5:
        _logger.info("Not enough demo products for sales history; skipping")
        return

    try:
        # Keep the static demo data counting: widen the recent-sales window.
        env['ir.config_parameter'].sudo().set_param(
            'alokai_recent_sales_count_days', str(lookback_days))

        rng = random.Random(2024)  # fixed seed -> identical demo every install

        # Per-product popularity weight: cubic skew -> few bestsellers, long tail
        weight = {t.id: rng.random() ** 3 for t in templates}

        # Group by first public category for themed (co-occurring) baskets
        by_cat = {}
        for t in templates:
            cat = t.public_categ_ids[:1].id or 0
            by_cat.setdefault(cat, []).append(t)
        cats = list(by_cat.keys())

        # Demo customers (search-or-create)
        Partner = env['res.partner']
        names = REVIEW_NAMES + [
            "Liam Walsh", "Nora Pereira", "Hugo Almeida", "Clara Nunes",
            "Marc Dubois", "Sofia Rossi", "Jonas Berg", "Aisha Khan",
            "Diego Castro", "Lena Fischer", "Tomas Silva", "Maya Patel",
            "Erik Larsen", "Chloe Martin", "Ravi Menon", "Greta Hoffmann",
            "Pablo Ortega", "Yuki Tanaka", "Sara Costa", "Noah Bauer",
        ]
        partners = []
        for name in names:
            p = Partner.search([('name', '=', name)], limit=1) or Partner.create({
                'name': name, 'company_type': 'person', 'customer_rank': 1,
            })
            partners.append(p)

        def weighted_sample(pool, k):
            pool = list(pool)
            chosen = []
            for _ in range(min(k, len(pool))):
                ws = [weight[t.id] + 0.01 for t in pool]
                t = rng.choices(pool, weights=ws, k=1)[0]
                chosen.append(t)
                pool.remove(t)
            return chosen

        now = datetime.now()
        batch, created, BATCH = [], 0, 500
        for _ in range(n_orders):
            cat = rng.choice(cats)
            picks = weighted_sample(by_cat[cat], rng.randint(1, 4))
            # occasional complementary item from another category
            if rng.random() < 0.35 and len(cats) > 1:
                other = rng.choice([c for c in cats if c != cat])
                picks += weighted_sample(by_cat[other], 1)

            lines = []
            for t in picks:
                lines.append((0, 0, {
                    'product_id': rng.choice(t.product_variant_ids).id,
                    'product_uom_qty': rng.randint(1, 3),
                }))
            if not lines:
                continue
            batch.append({
                'partner_id': rng.choice(partners).id,
                'date_order': now - timedelta(days=rng.randint(1, 180)),
                'state': 'sale',
                'client_order_ref': 'ALOKAI_DEMO',
                'order_line': lines,
            })
            if len(batch) >= BATCH:
                SaleOrder.create(batch)
                created += len(batch)
                batch = []
        if batch:
            SaleOrder.create(batch)
            created += len(batch)

        # Compute popularity + FBT from the new history
        env['product.template'].calculate_products_popularity()
        env['product.template'].calculate_frequently_bought_together()

        _logger.info(
            "Demo sales generated: %s orders; popularity + FBT computed", created)
    except Exception:
        _logger.exception("Demo sales generation failed; continuing install")


def _generate_demo_alternatives(env):
    """Populate alternative_product_ids on demo products (upsell strategy).

    For each demo product we pick a handful of *other* products from the same
    public category. To avoid a robotic, uniform look we vary both who and how
    many: the count per product is drawn from a non-uniform distribution (most
    get 2-4, a few get just 1 or as many as 6, some get none) and the picks are
    a random sample of category peers, so no two products share the same set.

    Deterministic (fixed seed); idempotent (skips if any demo product already
    has alternatives set).
    """
    imd = env['ir.model.data'].search([
        ('module', '=', 'graphql_alokai'),
        ('model', '=', 'product.template'),
    ])
    templates = env['product.template'].browse(imd.mapped('res_id')).exists()
    templates = templates.filtered(lambda t: t.sale_ok).sorted('id')
    if len(templates) < 3:
        _logger.info("Not enough demo products for alternatives; skipping")
        return
    if any(templates.mapped('alternative_product_ids')):
        _logger.info("Demo alternatives already set; skipping")
        return

    try:
        rng = random.Random(1337)  # fixed seed -> identical demo every install

        # Group by first public category; only peers within the same category
        # are sensible alternatives.
        by_cat = {}
        for t in templates:
            cat = t.public_categ_ids[:1].id or 0
            by_cat.setdefault(cat, []).append(t)

        # Non-uniform basket sizes: weighted toward 2-4, occasional 0/1/5/6.
        sizes = [0, 1, 2, 3, 4, 5, 6]
        size_weights = [3, 8, 22, 26, 22, 12, 7]

        updated = 0
        for t in templates:
            peers = [p for p in by_cat[t.public_categ_ids[:1].id or 0]
                     if p.id != t.id]
            if not peers:
                continue
            k = min(rng.choices(sizes, weights=size_weights, k=1)[0], len(peers))
            if not k:
                continue
            picks = rng.sample(peers, k)
            t.alternative_product_ids = [(6, 0, [p.id for p in picks])]
            updated += 1

        _logger.info("Demo alternatives set on %s products", updated)
    except Exception:
        _logger.exception("Demo alternatives generation failed; continuing install")


def _clear_unwanted_social_fields(env):
    """
    Odoo's own demo data populates social_facebook, social_youtube,
    social_instagram and social_tiktok on the main company. Those values then
    cascade into the website (via the field defaults).

    We only use X/LinkedIn/GitHub, so clear the rest here. The JSON-LD compute
    iterates dynamically over all social_* fields, so leaving them populated
    would pollute the Organization JSON-LD with URLs we don't actually own.
    """
    UNWANTED = ['social_facebook', 'social_youtube', 'social_instagram', 'social_tiktok', 'social_discord']
    cleared = 0

    # Clear on the main company (source of the website defaults)
    company = env.ref('base.main_company', raise_if_not_found=False)
    if company:
        vals = {f: False for f in UNWANTED if f in company._fields and company[f]}
        if vals:
            company.write(vals)
            cleared += len(vals)

    # Clear on every website (in case they were stored independently)
    for website in env['website'].search([]):
        vals = {f: False for f in UNWANTED if f in website._fields and website[f]}
        if vals:
            website.write(vals)
            cleared += len(vals)

    _logger.info("Cleared %s unwanted social fields", cleared)


def _fix_broken_blog_slugs(env):
    """
    Backfill blog.post.website_slug for posts whose stored value starts with
    'False/' or 'None/' - happens when the post was created before its parent
    blog had a slug, baking the literal "False" into the f-string output.

    Calls the compute method directly and flushes so the corrected slug is
    written to the database.
    """
    BlogPost = env['blog.post']
    bad_posts = BlogPost.search([
        '|',
        ('website_slug', '=like', 'False/%'),
        ('website_slug', '=like', 'None/%'),
    ])
    if not bad_posts:
        _logger.info("No broken blog slugs found; skipping fix")
        return

    bad_posts._compute_website_slug()
    bad_posts.flush_recordset(['website_slug'])
    _logger.info("Fixed %s blog post slugs with 'False/' or 'None/' prefix", len(bad_posts))


