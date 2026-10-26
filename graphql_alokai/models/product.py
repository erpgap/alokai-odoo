# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json
import re
import logging
import itertools
from odoo.fields import Domain
from collections import defaultdict
from urllib.parse import urlencode
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.http import request
from odoo.tools.float_utils import float_round
from odoo.exceptions import ValidationError
from psycopg2.extras import execute_values

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'website.slug.redis.mixin']

    def _invalidate_cms_pages(self):
        """Refresh the CMS pages that feature these products.

        This is the reverse lookup the mirrored product_tmpl_ids on each
        revision exists for. It filters to each page's LIVE revision: a page
        whose OLD revision mentioned a product is showing something else now,
        and invalidating it would refresh a page based on content nobody can
        see.

        Without this, a price or name change leaves featured-product blocks
        stale until their TTL expires.
        """
        if not self:
            return

        pages = self.env['alokai.website.page'].sudo().search([
            ('is_published', '=', True),
            ('live_revision_id.product_tmpl_ids', 'in', self.ids),
        ])
        if pages:
            self.env['invalidate.cache'].create_invalidate_cache(
                'alokai.website.page', pages.ids,
            )

    @api.model
    def _graphql_get_search_order(self, sort=None):
        sorting = ''

        if sort is not None:
            for field, val in sort.items():
                if sorting:
                    sorting += ', '
                if field == 'price':
                    sorting += 'list_price %s' % val.value
                elif field == 'popular':
                    sorting += 'recent_sales_count %s' % val.value
                elif field == 'newest':
                    # Products published before the date was recorded have
                    # none: sort them as the oldest, by creation date.
                    nulls = 'NULLS LAST' if val.value == 'DESC' else 'NULLS FIRST'
                    sorting += 'published_datetime %s %s, create_date %s' % (val.value, nulls, val.value)
                else:
                    sorting += '%s %s' % (field, val.value)

        # Add id as last factor, so we can consistently get the same results
        if sorting:
            sorting += ', id ASC'
        else:
            sorting = 'id ASC'

        return sorting

    @api.model
    def _graphql_in_stock_domain(self, website):
        """Products with Redis stock on ``website``, as a sub-select.

        Backed by the (website_id, quantity, product_id) index created in
        product.redis_stock.init(). Kept in SQL rather than reading the ids
        into Python and sending every one of them back in an ``IN`` list.
        """
        return [('product_tmpl_redis_stock_ids', 'any', [
            ('website_id', '=', website.id),
            ('quantity', '>', 0),
        ])]

    @api.model
    def _graphql_get_search_domain(self, search, **kwargs):
        env = self.env
        website = env['website'].get_current_website()

        # Only get published products
        domains = [
            website.sale_product_domain(),
            [('is_published', '=', True)],
        ]

        # Filter with ids
        if kwargs.get('ids', False):
            domains.append([('id', 'in', kwargs['ids'])])

        # Filter with Category ID
        if kwargs.get('category_id', False):
            domains.append([('public_categ_ids', 'child_of', kwargs['category_id'])])

        # Filter with Category Slug
        if kwargs.get('category_slug', False):
            # Resolve the slug against the current website first (same domain
            # the category resolver uses): a category restricted to another
            # website must not expose its products here. No match returns an
            # empty product list, consistent with the category page itself.
            category_domain = website.website_domain()
            category_domain += [('website_slug', '=', kwargs['category_slug'])]
            categories = env['product.public.category'].sudo().search(category_domain)
            # The category and its descendants, resolved up front: passing the
            # ids as a list lets Postgres estimate the match from its column
            # statistics, where a child_of subquery left it guessing - and on
            # large catalogues picking a plan several times slower.
            categories = categories.search([('id', 'child_of', categories.ids)])
            domains.append([('public_categ_ids', 'in', categories.ids)])

        # Filter With Name
        if kwargs.get('name', False):
            name = kwargs['name']
            for n in name.split(" "):
                domains.append([('name', 'ilike', n)])

        # Stock
        if kwargs.get('in_stock', False):
            domains.append(self._graphql_in_stock_domain(website))

        # Every word must match the name or the internal reference. The sales
        # description is left out: matching inside long marketing text returns
        # unrelated products, and it cannot use an index.
        if search:
            for srch in search.split():
                domains.append(['|', ('name', 'ilike', srch), ('default_code', 'ilike', srch)])

        # Used for improving attributes filtering
        attributes_partial_domain = domains.copy()

        # Filter with Attribute Value
        filtered_attributes = {}
        if kwargs.get('attrib_values', False):
            attributes_domain = []

            for value in kwargs['attrib_values']:
                try:
                    value = value.split('-')
                    if len(value) != 2:
                        continue

                    attribute_id = int(value[0])
                    attribute_value_id = int(value[1])
                except ValueError:
                    continue

                if attribute_id not in filtered_attributes:
                    filtered_attributes[attribute_id] = []

                filtered_attributes[attribute_id].append(attribute_value_id)

            for key, value in filtered_attributes.items():
                attributes_domain.append([('attribute_line_ids.value_ids', 'in', value)])

            attributes_domain = Domain.AND(attributes_domain)
            domains.append(attributes_domain)

        # Min and max price of recordset need to be calculated without the price filter
        prices_partial_domain = domains.copy()

        # Product Price Filter. The attribute facet counts must honour it too,
        # so it also goes into the attributes partial domain.
        price_domains = []
        if kwargs.get('min_price', False):
            price_domains.append([('list_price', '>=', float(kwargs['min_price']))])
        if kwargs.get('max_price', False):
            price_domains.append([('list_price', '<=', float(kwargs['max_price']))])
        domains += price_domains
        attributes_partial_domain += price_domains

        return (
            domains,
            attributes_partial_domain,
            prices_partial_domain,
            filtered_attributes
        )

    def _alokai_variant_shop_prices(self, website):
        """The shop price of every variant of these templates, in one pass.

        The same number `_get_combination_info_variant()['price']` gives - the
        pricelist price with the product's taxes, mapped by the fiscal
        position, applied the way the website displays them - but priced for
        all variants in one pricelist call. Per variant, combination info also
        works out stock, wishlist and discount data the JSON-LD never reads,
        which made it most of the cost of a product page's query.

        Outside a shop request (no pricelist on the request) it falls back to
        combination info, which knows how to find one.
        """
        variants = self.product_variant_ids
        if not variants:
            return {}
        pricelist = request and getattr(request, 'pricelist', None)
        if not pricelist:
            return {v.id: v._get_combination_info_variant()['price'] for v in variants}

        currency = website.currency_id
        rule_prices = pricelist.with_context(self.env.context)._compute_price_rule(
            variants, 1.0, currency=currency,
        )
        fiscal_position = request.fiscal_position
        company = self.env.company
        prices = {}
        for variant in variants:
            price = rule_prices[variant.id][0]
            product_taxes = variant.sudo().taxes_id._filter_taxes_by_company(company)
            if product_taxes:
                price = self._apply_taxes_to_price(
                    price, currency, product_taxes, fiscal_position.map_tax(product_taxes),
                    variant, website=website,
                )
            prices[variant.id] = price
        return prices

    # Variant axes schema.org has a property for. Google reads them from
    # `variesBy` and from each variant; any other axis is described with
    # additionalProperty instead.
    _ALOKAI_SCHEMA_AXES = {
        'color': 'color',
        'colour': 'color',
        'size': 'size',
        'material': 'material',
        'pattern': 'pattern',
    }

    @staticmethod
    def _alokai_slugify_axis_value(name):
        """The storefront's slugifyValue (layers/product/utils): "Off White" -> "off-white"."""
        return re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')

    def _alokai_variant_path(self, variant):
        """The storefront URL path of one variant, as mountUrlSlugForProductVariant builds it.

        The template slug plus one query parameter per variant axis: the
        attribute name lowercased, and the value as "<slug>-<ptav id>" (or the
        bare id when the name has nothing to slugify).
        """
        params = []
        for ptav in variant.product_template_attribute_value_ids:
            slug = self._alokai_slugify_axis_value(ptav.name)
            params.append((ptav.attribute_id.name.lower(), f'{slug}-{ptav.id}' if slug else str(ptav.id)))
        path = self.website_slug or ''
        return f'{path}?{urlencode(params)}' if params else path

    def _compute_json_ld(self):
        """schema.org markup for the product page.

        A template with several variants is described as a ProductGroup with
        one Product per variant, each with its own Offer - its price, its
        stock, its URL. That is the shape Google reads for products whose
        variants share a page: here every variant URL (?color=...) declares
        the template URL as canonical, so the template page is the one that
        is indexed and must describe all of them. A single AggregateOffer
        spanning the variants made the product ineligible for merchant
        listings, which need a concrete price.

        A template with one variant stays a Product with one Offer.
        """
        env = self.env
        website = env['website'].get_current_website()
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base_url and base_url[-1:] == '/':
            base_url = base_url[:-1]

        website_domain = website._alokai_domain()
        currency = website.currency_id.name

        seller_name = (website and website.display_name) or env.user.company_id.display_name
        shop_prices = self._alokai_variant_shop_prices(website)

        def availability(record):
            # The website-scoped, Redis-backed stock flag (the same source as
            # is_in_stock) rather than a live qty_available aggregation.
            in_stock = record.has_stock if record.is_storable else True
            return "https://schema.org/InStock" if in_stock else "https://schema.org/OutOfStock"

        def offer(url, price, record):
            return {
                "@type": "Offer",
                "url": url,
                "priceCurrency": currency,
                "price": f"{price:.2f}",
                "itemCondition": "https://schema.org/NewCondition",
                "availability": availability(record),
                "seller": {
                    "@type": "Organization",
                    "name": seller_name,
                },
            }

        # bin_size: only whether an image exists is needed, not its bytes.
        sized = self.with_context(bin_size=True)

        for product, product_sized in zip(self, sized):
            images = []
            if product_sized.image_1920:
                images.append(f'{base_url}/web/image/product.template/{product.id}/image_1920')

            product_url = f"{website_domain}{product.website_slug or ''}"
            variants = product.product_variant_ids

            if len(variants) > 1:
                axes = []
                has_variant = []
                for variant, variant_sized in zip(variants, variants.with_context(bin_size=True)):
                    item = {
                        "@type": "Product",
                        "name": variant.with_context(display_default_code=False).display_name,
                        "offers": offer(
                            f"{website_domain}{product._alokai_variant_path(variant)}",
                            shop_prices[variant.id],
                            variant,
                        ),
                    }
                    if variant.default_code:
                        item["sku"] = variant.default_code
                    if variant_sized.image_variant_1920:
                        item["image"] = f'{base_url}/web/image/product.product/{variant.id}/image_1920'
                    elif images:
                        item["image"] = images[0]

                    extra = []
                    for ptav in variant.product_template_attribute_value_ids:
                        attribute = ptav.attribute_id.name
                        schema_property = self._ALOKAI_SCHEMA_AXES.get(attribute.lower())
                        if schema_property:
                            item[schema_property] = ptav.name
                            if schema_property not in axes:
                                axes.append(schema_property)
                        else:
                            extra.append({"@type": "PropertyValue", "name": attribute, "value": ptav.name})
                    if extra:
                        item["additionalProperty"] = extra
                    has_variant.append(item)

                json_ld = {
                    "@context": "https://schema.org",
                    "@type": "ProductGroup",
                    "name": product.with_context(display_default_code=False).display_name,
                    "url": product_url,
                    "productGroupID": product.default_code or str(product.id),
                    "image": images,
                    "brand": {
                        "@type": "Brand",
                        "name": seller_name,
                    },
                    "hasVariant": has_variant,
                }
                if axes:
                    json_ld["variesBy"] = [f"https://schema.org/{axis}" for axis in axes]
            else:
                price = shop_prices[variants.id] if variants else product._get_combination_info()['price']
                json_ld = {
                    "@context": "https://schema.org",
                    "@type": "Product",
                    "name": product.with_context(display_default_code=False).display_name,
                    "image": images,
                    "brand": {
                        "@type": "Brand",
                        "name": seller_name,
                    },
                    "offers": offer(product_url, price, product),
                }
                # For a single-variant template default_code is the variant's.
                sku = product.default_code or product.variants_default_code
                if sku:
                    json_ld["sku"] = sku

            if product.description_sale:
                json_ld["description"] = product.description_sale

            # Category: hierarchical path like "Women > Clothing > Dresses"
            if product.public_categ_ids:
                deepest = product.public_categ_ids[0]
                parts = []
                node = deepest
                while node:
                    parts.append(node.name)
                    node = node.parent_id
                parts.reverse()
                json_ld["category"] = ' > '.join(parts)

            # aggregateRating: only include if there are actual reviews.
            # Google prefers numeric values as strings in structured data.
            if getattr(product, 'rating_count', 0) > 0:
                json_ld["aggregateRating"] = {
                    "@type": "AggregateRating",
                    "ratingValue": f"{product.sudo().rating_avg:.2f}",
                    "reviewCount": str(product.rating_count),
                    "bestRating": "5",
                    "worstRating": "1",
                }

            product.json_ld = json.dumps(json_ld)

    def _get_breadcrumb_category(self, categories, category):
        categories.append({
            'name': category.name,
            'slug': category.website_slug,
        })
        if category.parent_id:
            categories = self._get_breadcrumb_category(categories, category.parent_id)
        return categories

    def _compute_breadcrumb(self):
        for product in self:
            categories = []
            if product.public_categ_ids[0]:
                categories = product._get_breadcrumb_category([], product.public_categ_ids[0])
                categories.reverse()
            product.breadcrumb = json.dumps(categories)

    def get_json_ld_breadcrumb(self):
        items = []

        if self.public_categ_ids:
            website = self.env['website'].get_current_website()
            domain = website._alokai_domain()

            categories = self._get_breadcrumb_category([], self.public_categ_ids[0])
            categories.reverse()

            for index, category in enumerate(categories):
                items.append({
                    "@type": "ListItem",
                    "position": index + 1,
                    "name": category['name'],
                    "item": f"{domain}{category['slug']}"
                })

        return {
            "@context": "https://schema.org/",
            "@type": "BreadcrumbList",
            "itemListElement": items
        }

    @api.depends('name')
    def _compute_website_slug(self):
        langs = self.env['res.lang'].search([])

        for product in self:
            for lang in langs:
                product = product.with_context(lang=lang.code)

                if not product.id:
                    product.website_slug = None
                else:
                    prefix = '/product'
                    slug_name = self.env['ir.http']._slugify(product.name or '').strip().strip('-')
                    product.website_slug = f'{prefix}/{slug_name}-{product.id}'

    def _compute_recent_sales_count(self):
        # Skip unsaved (NewId) records - only persisted rows have integer ids
        self = self.filtered(lambda p: isinstance(p.id, int))

        lookback_days = int(self.env['ir.config_parameter'].sudo().get_param('alokai_recent_sales_count_days', 30))
        date_days_ago = fields.Datetime.now() - timedelta(days=lookback_days)
        done_states = self.env['sale.report'].sudo()._get_done_states()
        domain = [
            ('state', 'in', done_states),
            ('date', '>=', date_days_ago),
        ]

        sale_groups = self.env['sale.report'].sudo()._read_group(
            domain,
            ['product_id', 'product_uom_qty'],
            ['product_id:sum'],
        )
        # TODO: check why product id is False in sale.report
        sale_count_map = {group[0].id: group[1] for group in sale_groups if group[0]}

        raw = []

        for product in self:
            if product.type in ['product', 'consu']:
                product_id = product.product_variant_id.id
                sales_count = sale_count_map.get(product_id, 0)
                sales_count = float_round(sales_count, precision_rounding=product.uom_id.rounding)
                recent_sales_count = sales_count + product.recent_sales_count_increment
            else:
                recent_sales_count = 0

            cat_id = product.public_categ_ids[:1].id or 0
            raw.append((recent_sales_count, product.id, cat_id))

        # Normalize per category so no single category dominates the popular sort.
        # Each product's score is scaled to [0, 1] within its category, then
        # multiplied by 100 to keep readable values.
        cat_max = {}
        for score, _pid, cat_id in raw:
            if score > cat_max.get(cat_id, 0):
                cat_max[cat_id] = score

        values = []
        for score, pid, cat_id in raw:
            max_in_cat = cat_max.get(cat_id, 0)
            if max_in_cat > 0:
                normalized = (score / max_in_cat) * 100
            else:
                normalized = 0
            values.append((normalized, pid))

        if values:
            query = f"""
                UPDATE product_template AS t
                SET recent_sales_count = v.recent_sales_count
                FROM (VALUES %s) AS v(recent_sales_count, id)
                WHERE v.id = t.id
            """
            execute_values(self.env.cr, query, values)

    @api.depends('published_datetime')
    def _compute_published_hours(self):
        for product in self:
            if product.published_datetime:
                delta = datetime.now() - product.published_datetime
                product.published_hours = int(delta.total_seconds() // 3600)
            else:
                product.published_hours = 0

    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)
    recent_sales_count = fields.Float('Recent Sales Count', compute='_compute_recent_sales_count', store=True,
                                      readonly=True)
    recent_sales_count_increment = fields.Integer('Recent Sales Count Increment', default=0, required=True)
    frequently_bought_together_ids = fields.One2many('product.template.fbt', 'product_id', 'Frequently Bought Together',
                                                     readonly=True)
    product_tmpl_redis_stock_ids = fields.One2many('product.template.redis_stock', 'product_id', 'Template Redis Stock',
                                                   readonly=True)
    published_datetime = fields.Datetime('Published On', help='Datetime when the product was published', readonly=True,
                                         copy=False)
    published_hours = fields.Integer('Hours Published', compute='_compute_published_hours',
                                     help='Total hours the product has been published', readonly=True)
    alokai_page_ids = fields.Many2many(
        'alokai.website.page', 'product_template_alokai_website_page_rel', 'product_tmpl_id', 'alokai_page_id',
        string='Alokai Website Pages'
    )
    has_stock = fields.Boolean(string='Has Stock', compute='_compute_has_stock', search='_search_has_stock',
                               store=False)

    def _compute_has_stock(self):
        website = self.env['website'].get_current_website()
        self.env.cr.execute("""
            SELECT DISTINCT product_id
            FROM product_template_redis_stock
            WHERE website_id = %s AND quantity > 0
        """, (website.id,))
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products_with_stock = set(product_ids)

        for product in self:
            product.has_stock = product.id in products_with_stock

    def _search_has_stock(self, operator, value):
        website = self.env['website'].get_current_website()
        self.env.cr.execute("""
            SELECT DISTINCT product_id
            FROM product_template_redis_stock
            WHERE website_id = %s AND quantity > 0
        """, (website.id,))
        product_ids = [row[0] for row in self.env.cr.fetchall()]

        # We only handle True/False filters here
        if (operator in ('=', '==') and value) or (operator == '!=' and not value):
            # has_stock = True
            return [('id', 'in', product_ids)]
        else:
            # has_stock = False
            return [('id', 'not in', product_ids)]

    def _get_product_placeholder_filename(self):
        """Return the placeholder shown for products without an image.

        Only used when the ``alokai_product_placeholder_image`` system parameter
        points to a module path (our fashion demo data sets it to the branded
        image); otherwise Odoo's own generic product placeholder is kept, so a
        real customer catalogue never gets a themed image it did not ask for.
        product.product._get_product_placeholder_filename delegates to the
        template, so this single override covers both templates and variants.
        """
        placeholder = self.env['ir.config_parameter'].sudo().get_param('alokai_product_placeholder_image')
        return placeholder or super()._get_product_placeholder_filename()

    def action_open_storefront(self):
        """Smart-button action: open this product on the configured website domain
        (i.e. the Nuxt storefront, not Odoo's built-in /shop)."""
        self.ensure_one()
        website = self.env['website'].get_current_website()
        base_url = website._alokai_domain()
        url = f"{base_url}{self.website_slug or ''}" if self.website_slug else base_url
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    @staticmethod
    def _publishes(vals):
        # The backend toggles is_published; website_published is the website
        # mixin's alias for it (the frontend publish button writes that one).
        return bool(vals.get('is_published') or vals.get('website_published'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if self._publishes(vals):
                vals.setdefault('published_datetime', fields.Datetime.now())

        return super(ProductTemplate, self).create(vals_list)

    def write(self, vals):
        # Only a first publication is dated: republishing keeps the original
        # date. Written apart from the rest so a product already dated in the
        # same batch keeps its own.
        first_published = self.browse()
        if self._publishes(vals) and 'published_datetime' not in vals:
            first_published = self.filtered(lambda p: not p.is_published and not p.published_datetime)
        if first_published:
            super(ProductTemplate, first_published).write(
                dict(vals, published_datetime=fields.Datetime.now()))
        if self - first_published:
            super(ProductTemplate, self - first_published).write(vals)

        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        self._invalidate_cms_pages()

        return True

    def unlink(self):
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        self._invalidate_cms_pages()
        return super(ProductTemplate, self).unlink()

    def _get_combination_info(self, combination=False, product_id=False, add_qty=1, parent_combination=False,
                              only_template=False, **kwargs):
        """ Add discount value and percentage based """
        # Build kwargs dynamically to support different Odoo versions
        call_kwargs = {
            'combination': combination,
            'product_id': product_id,
            'add_qty': add_qty,
            'only_template': only_template
        }
        # Only pass parent_combination if the parent method supports it
        import inspect
        parent_method = super(ProductTemplate, self)._get_combination_info
        if 'parent_combination' in inspect.signature(parent_method).parameters:
            call_kwargs['parent_combination'] = parent_combination

        # Pass any additional kwargs (e.g., uom_id from Odoo 19)
        call_kwargs.update(kwargs)

        combination_info = parent_method(**call_kwargs)

        discount = 0
        discount_perc = 0
        if combination_info['has_discounted_price'] and product_id:
            discount = combination_info['list_price'] - combination_info['price']
            discount_perc = combination_info['list_price'] and (discount * 100 / combination_info['list_price']) or 0

        combination_info.update({
            'discount': round(discount, 2),
            'discount_perc': int(round(discount_perc, 2)),
        })

        return combination_info

    @api.model
    def calculate_products_popularity(self):
        self.search([])._compute_recent_sales_count()

    @api.model
    def calculate_frequently_bought_together(self):
        lookback_days = int(self.env['ir.config_parameter'].sudo().get_param('alokai_recent_sales_count_days', 30))
        date_days_ago = fields.Datetime.now() - timedelta(days=lookback_days)
        done_states = self.env['sale.report'].sudo()._get_done_states()
        domain = [
            ('state', 'in', done_states),
            ('date', '>=', date_days_ago),
        ]
        sale_groups = self.env['sale.report'].search(domain)

        order_to_products = defaultdict(list)
        for sale_group in sale_groups:
            if sale_group.product_id.type in ['product', 'consu']:
                order_id = sale_group.order_reference
                product_id = sale_group.product_id.product_tmpl_id.id
                qty = sale_group.product_uom_qty
                order_to_products[order_id].append((product_id, qty))

        product_relations = defaultdict(lambda: defaultdict(float))
        for _, products in order_to_products.items():
            # For each order, track pairs of products and add their quantities
            for i in range(len(products)):
                for j in range(i + 1, len(products)):
                    product_a, qty_a = products[i]
                    product_b, qty_b = products[j]
                    # Add the quantities of the products to each other (since it's symmetric)
                    product_relations[product_a][product_b] += min(qty_a, qty_b)
                    product_relations[product_b][product_a] += min(qty_a, qty_b)

        cr = self.env.cr
        cr.execute('TRUNCATE TABLE product_template_fbt RESTART IDENTITY CASCADE')

        values = []
        for product_id, related_products in product_relations.items():
            related_product_pairs = sorted(related_products.items(), key=lambda p: -p[1])
            for related_product_id, qty in related_product_pairs:
                values.append((product_id, related_product_id, qty))

        if values:
            query = f"""
                INSERT INTO product_template_fbt (product_id, related_product_id, qty)
                VALUES %s
            """
            execute_values(cr, query, values)

    def _has_no_variant_attributes(self):
            """ Overwrite : always return False regardless of product attributes variant creation mode setting
            to avoid create multiple sale order line for same product
            """
            self.ensure_one()
            return False


class ProductTemplateFBT(models.Model):
    _name = 'product.template.fbt'
    _description = 'Frequently Bought Together'
    _order = 'qty DESC'

    product_id = fields.Many2one('product.template', 'Product', required=True, ondelete='cascade')
    related_product_id = fields.Many2one('product.template', 'Related Product', required=True, ondelete='cascade')
    qty = fields.Float('Quantity', default=0.0, required=True)

    def _has_no_variant_attributes(self):
        """ Overwrite : always return False regardless of product attributes variant creation mode setting
        to avoid create multiple sale order line for same product
        """
        self.ensure_one()
        return False


class ProductProduct(models.Model):
    _inherit = 'product.product'

    product_redis_stock_ids = fields.One2many('product.product.redis_stock', 'product_id', 'Variant Redis Stock', readonly=True)
    has_stock = fields.Boolean(string='Has Stock', compute='_compute_has_stock', search='_search_has_stock',
                               store=False)

    def _get_placeholder_filename(self, field):
        # Core only special-cases image_1920/1024/512/256/128; variant-specific
        # image_variant_* fields fall through to Odoo's default placeholder otherwise.
        if field in ('image_variant_%s' % size for size in (1920, 1024, 512, 256, 128)):
            return self._get_product_placeholder_filename()
        return super()._get_placeholder_filename(field)

    def _compute_has_stock(self):
        website = self.env['website'].get_current_website()
        self.env.cr.execute("""
            SELECT DISTINCT product_id
            FROM product_product_redis_stock
            WHERE website_id = %s AND quantity > 0
        """, (website.id,))
        product_ids = [row[0] for row in self.env.cr.fetchall()]
        products_with_stock = set(product_ids)

        for product in self:
            product.has_stock = product.id in products_with_stock

    def _search_has_stock(self, operator, value):
        website = self.env['website'].get_current_website()
        self.env.cr.execute("""
            SELECT DISTINCT product_id
            FROM product_product_redis_stock
            WHERE website_id = %s AND quantity > 0
        """, (website.id,))
        product_ids = [row[0] for row in self.env.cr.fetchall()]

        # We only handle True/False filters here
        if (operator in ('=', '==') and value) or (operator == '!=' and not value):
            # has_stock = True
            return [('id', 'in', product_ids)]
        else:
            # has_stock = False
            return [('id', 'not in', product_ids)]

    @api.model
    def _update_dirty_products_stock_redis(self):
        # Redis is opt-in; skip entirely when disabled (also covers tests).
        if not self.env['website']._redis_enabled():
            return

        redis_client = self.env['website']._redis_connect()
        if not redis_client:
            return
        try:
            # Cap work per run; leftover keys roll to the next tick since we
            # only delete the keys we scanned. At current scale this never
            # fires (catalog < 5000), it's just a safety valve for growth.
            dirty_keys = list(itertools.islice(redis_client.scan_iter('stock:product-is-dirty-*'), 5000))
            if not dirty_keys:
                return
            if len(dirty_keys) >= 5000:
                _logger.warning(
                    "Alokai stock: dirty-key batch capped at 5000; the rest will "
                    "sync on the next run.")

            # Skip None (key deleted concurrently) and dedupe: multiple keys
            # per product is now expected (uuid-suffixed flags).
            values = [redis_client.get(k) for k in dirty_keys]
            product_ids = list({int(v) for v in values if v is not None})
            if product_ids:
                # The scan above can observe flags for changes that committed
                # AFTER this cron's REPEATABLE READ snapshot began. Reset the
                # snapshot before reading free_qty so every scanned flag's change
                # is reflected; otherwise we could compute a stale value and then
                # delete its flag, leaving Redis permanently out of sync until
                # the next change to that product or the daily resync. Safe: the
                # dirty cron is the single writer and nothing is written above.
                self.env.cr.commit()
                self.env.invalidate_all()

                products = self.search([('id', 'in', product_ids)])
                products._update_products_stock_redis(redis_client)

            # Delete the scanned flags only after the update above succeeded: if
            # it raised, we never get here, the flags survive and the products
            # are retried on the next run.
            for dirty_key in dirty_keys:
                redis_client.delete(dirty_key)
        finally:
            redis_client.close()

    @api.model
    def _update_all_products_stock_redis(self):
        # Redis is opt-in; skip entirely when disabled (also covers tests).
        if not self.env['website']._redis_enabled():
            return
        # Don't write the redis_stock tables directly here. Instead flag every
        # product dirty and let the dirty cron sync them. That keeps a single
        # writer to those tables, avoiding REPEATABLE READ serialization errors
        # when this full resync would otherwise overlap the dirty cron.
        product_ids = self.search([]).ids
        self.env['stock.quant']._write_dirty_keys_redis(product_ids)

    def _update_products_stock_redis(self, redis_client):
        if not self:
            return

        StockWarehouse = self.env['stock.warehouse']
        pipe = redis_client.pipeline()

        product_tmpls = self.mapped('product_tmpl_id')
        websites = self.env['website'].search([])

        website_warehouses_map = {
            website.id: StockWarehouse.search([('company_id', '=', website.company_id.id)]).mapped('lot_stock_id').ids
            for website in websites
        }

        product_stock_values = []
        template_stock_values = []

        for product in self:
            data = {}
            for website in websites:
                lot_stock_ids = website_warehouses_map.get(website.id, [])
                free_qty = product.with_context(location=lot_stock_ids).free_qty
                data[website.id] = free_qty
                product_stock_values.append((product.id, website.id, free_qty))
            pipe.set(f'stock:product-{product.id}', json.dumps(data))

        for product_tmpl in product_tmpls:
            for website in websites:
                lot_stock_ids = website_warehouses_map.get(website.id, [])
                free_qty = sum(product_tmpl.product_variant_ids.with_context(location=lot_stock_ids).mapped('free_qty'))
                template_stock_values.append((product_tmpl.id, website.id, free_qty))

        # Redis is the store the storefront actually reads for stock, so write it
        # first: even if the (secondary) Postgres tables fail afterwards, Redis
        # already reflects the sale. A Postgres failure raises, so the caller
        # skips deleting the flags and the row is retried on the next run.
        pipe.execute()

        self.env['product.product.redis_stock'].bulk_update_redis_stock(product_stock_values)
        self.env['product.template.redis_stock'].bulk_update_redis_stock(template_stock_values)


class ProductStockRedis(models.AbstractModel):
    _name = 'product.redis_stock'
    _description = 'Redis Stock (base)'

    website_id = fields.Many2one('website', 'Website', required=True)
    quantity = fields.Float('Quantity', digits='Product Unit of Measure', required=True)

    def init(self):
        super().init()
        # Index the in-stock lookup (WHERE website_id = %s AND quantity > 0,
        # returning product_id) run on every product listing. Created for each
        # concrete table (template + variant) via the shared self._table; the
        # abstract base has no table, so skip it.
        if self._abstract:
            return
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS %(name)s ON %(table)s "
            "(website_id, quantity, product_id)" % {
                'name': '%s_website_qty_idx' % self._table,
                'table': self._table,
            })

    @api.model
    def bulk_update_redis_stock(self, values):
        """Efficiently upsert multiple redis stock records.

        :param values: list of tuples (product_id, website_id, quantity)
        """
        if not values:
            return

        query = f"""
            INSERT INTO {self._table} (product_id, website_id, quantity)
            VALUES %s
            ON CONFLICT (product_id, website_id)
            DO UPDATE SET quantity = EXCLUDED.quantity
        """

        execute_values(self.env.cr, query, values, page_size=1000)


class ProductProductRedisStock(models.Model):
    _name = 'product.product.redis_stock'
    _inherit = 'product.redis_stock'
    _description = 'Product Variant Redis Stock'

    product_id = fields.Many2one('product.product', 'Product', required=True, ondelete='cascade')

    _unique_product_website = models.Constraint(
        'unique(product_id, website_id)',
        'Product and Website must be unique!',
    )


class ProductTemplateRedisStock(models.Model):
    _name = 'product.template.redis_stock'
    _inherit = 'product.redis_stock'
    _description = 'Product Template Redis Stock'

    product_id = fields.Many2one('product.template', 'Product', required=True, ondelete='cascade')

    _unique_template_website = models.Constraint(
        'unique(product_id, website_id)',
        'Template and Website must be unique!',
    )


class ProductPublicCategory(models.Model):
    _name = 'product.public.category'
    _inherit = ['product.public.category', 'website.slug.redis.mixin']

    def action_open_storefront(self):
        """Smart-button action: open this category on the configured website
        domain (the Nuxt storefront, not Odoo's built-in /shop)."""
        self.ensure_one()
        website = self.env['website'].get_current_website()
        base_url = website._alokai_domain()
        url = f"{base_url}{self.website_slug or ''}" if self.website_slug else base_url
        return {
            'type': 'ir.actions.act_url',
            'url': url,
            'target': 'new',
        }

    def _compute_json_ld(self):
        env = self.env
        website = env['website'].get_current_website()
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base_url and base_url[-1:] == '/':
            base_url = base_url[:-1]

        website_domain = website._alokai_domain()

        for category in self:
            category_url = f'{website_domain}{category.website_slug or ""}'

            # Walk parents (root -> current) for breadcrumb
            ancestors = []
            node = category
            while node:
                ancestors.append(node)
                node = node.parent_id
            ancestors.reverse()

            breadcrumb_items = [
                {
                    "@type": "ListItem",
                    "position": idx,
                    "name": ancestor.name,
                    "item": f'{website_domain}{ancestor.website_slug or ""}',
                }
                for idx, ancestor in enumerate(ancestors, 1)
            ]

            # Sample products in this category (capped to keep JSON-LD reasonable)
            products = env['product.template'].search([
                ('public_categ_ids', 'in', category.id),
                ('is_published', '=', True),
            ], limit=20)

            # Use the proper Schema.org ListItem.item structure with full Product
            # entity. This is richer than just url+name and lets us add image/price
            # later if we want.
            item_list_elements = [
                {
                    "@type": "ListItem",
                    "position": idx,
                    "item": {
                        "@type": "Product",
                        "name": product.with_context(display_default_code=False).display_name,
                        "url": f'{website_domain}{product.website_slug or ""}',
                    },
                }
                for idx, product in enumerate(products, 1)
            ]

            json_ld = {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "url": category_url,
                "name": category.display_name,
            }

            if category.website_meta_description:
                json_ld["description"] = category.website_meta_description

            if breadcrumb_items:
                json_ld["breadcrumb"] = {
                    "@type": "BreadcrumbList",
                    "itemListElement": breadcrumb_items,
                }

            if item_list_elements:
                json_ld["mainEntity"] = {
                    "@type": "ItemList",
                    "numberOfItems": len(item_list_elements),
                    "itemListElement": item_list_elements,
                }

            category.json_ld = json.dumps(json_ld)

    def _get_breadcrumb_category(self, categories, category):
        categories.append({
            'name': category.name,
            'slug': category.website_slug,
        })
        if category.parent_id:
            categories = self._get_breadcrumb_category(categories, category.parent_id)
        return categories

    def _compute_breadcrumb(self):
        for category in self:
            categories = category._get_breadcrumb_category([], category)
            categories.reverse()
            category.breadcrumb = json.dumps(categories)

    def _validate_website_slug(self):
        for category in self.filtered(lambda c: c.website_slug):
            if category.website_slug[0] != '/':
                raise ValidationError(_('Slug should start with /'))

            if self.search([('website_slug', '=', category.website_slug), ('id', '!=', category.id)], limit=1):
                raise ValidationError(_('Slug is already in use: {}'.format(category.website_slug)))

    website_slug = fields.Char('Website Slug', translate=True, copy=False)
    attribute_ids = fields.Many2many('product.attribute', string='Filtering Attributes')

    @api.model_create_multi
    def create(self, vals_list):
        res = super(ProductPublicCategory, self).create(vals_list)

        for rec in res:
            if rec.website_slug:
                rec._validate_website_slug()
            else:
                rec.website_slug = f'/category/{rec.id}'

        return res

    def write(self, vals):
        res = super(ProductPublicCategory, self).write(vals)
        if vals.get('website_slug', False):
            self._validate_website_slug()
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return res

    def unlink(self):
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return super(ProductPublicCategory, self).unlink()


class ProductAttributeValue(models.Model):
    _inherit = 'product.attribute.value'

    visibility = fields.Selection(related='attribute_id.visibility', store=True, readonly=True)


class ProductTag(models.Model):
    _inherit = 'product.tag'

    background_color = fields.Char('Background Color', default='#ffffff')
