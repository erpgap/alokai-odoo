# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json
from odoo.osv import expression
from collections import defaultdict
from datetime import datetime, timedelta
from odoo import models, fields, api, _
from odoo.tools.float_utils import float_round
from odoo.addons.http_routing.models.ir_http import slugify
from odoo.exceptions import ValidationError
from psycopg2.extras import execute_values


class ProductTemplate(models.Model):
    _inherit = 'product.template'

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
                    sorting += 'published_datetime %s, create_date %s' % (val.value, val.value)
                else:
                    sorting += '%s %s' % (field, val.value)

        # Add id as last factor, so we can consistently get the same results
        if sorting:
            sorting += ', id ASC'
        else:
            sorting = 'id ASC'

        return sorting

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
            domains.append([('public_categ_slug_ids.website_slug', '=', kwargs['category_slug'])])

        # Filter With Name
        if kwargs.get('name', False):
            name = kwargs['name']
            for n in name.split(" "):
                domains.append([('name', 'ilike', n)])

        # Stock
        if kwargs.get('in_stock', False):
            # TODO:
            # Possible index to improve performance
            # CREATE INDEX idx_redis_stock_website_quantity
            # ON product_template_redis_stock (website_id, quantity, product_id);
            self.env.cr.execute("""
                SELECT DISTINCT product_id
                FROM product_template_redis_stock
                WHERE website_id = %s AND quantity > 0
            """, (website.id,))
            product_ids = [row[0] for row in self.env.cr.fetchall()]
            domains.append([('id', 'in', product_ids)])

        if search:
            for srch in search.split(" "):
                domains.append([
                    '|', '|', ('name', 'ilike', srch), ('description_sale', 'ilike', srch), ('default_code', 'ilike', srch)])

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

            attributes_domain = expression.AND(attributes_domain)
            domains.append(attributes_domain)

        # Min and max price of recordset need to be calculated without the price filter
        prices_partial_domain = domains.copy()

        # Product Price Filter
        if kwargs.get('min_price', False):
            domains.append([('list_price', '>=', float(kwargs['min_price']))])
        if kwargs.get('max_price', False):
            domains.append([('list_price', '<=', float(kwargs['max_price']))])

        return (
            domains,
            attributes_partial_domain,
            prices_partial_domain,
            filtered_attributes
        )

    def _compute_json_ld(self):
        env = self.env
        website = env['website'].get_current_website()
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base_url and base_url[-1:] == '/':
            base_url = base_url[:-1]

        website_domain = website.domain or ''
        if website_domain and website_domain[-1:] == '/':
            website_domain = website_domain[:-1]

        for product in self:
            # Get list of images
            images = list()
            if product.image_1920:
                images.append(f'{base_url}/web/image/product.template/{product.id}/image_1920')

            json_ld = {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": product.display_name,
                "image": images,
                "offers": {
                    "@type": "Offer",
                    "url": f"{website_domain}{product.website_slug}",
                    "priceCurrency": product.currency_id.name,
                    "price": product.list_price,
                    "itemCondition": "https://schema.org/NewCondition",
                    "availability": "https://schema.org/InStock",
                    "seller": {
                        "@type": "Organization",
                        "name": website and website.display_name or product.env.user.company_id.display_name
                    }
                }
            }

            if product.description_sale:
                json_ld.update({"description": product.description_sale})

            if product.default_code:
                json_ld.update({"sku": product.default_code})

            product.json_ld = json.dumps(json_ld)

    def _get_public_categ_slug(self, category_ids, category):
        category_ids.append(category.id)

        if category.parent_id:
            category_ids = self._get_public_categ_slug(category_ids, category.parent_id)

        return category_ids

    @api.depends('public_categ_ids')
    def _compute_public_categ_slug_ids(self):
        """ To allow search of website_slug on parent categories """
        cr = self.env.cr

        for product in self:
            category_ids = []

            for category in product.public_categ_ids:
                category_ids = product._get_public_categ_slug(category_ids, category)

            cr.execute("""
                DELETE FROM product_template_product_public_category_slug_rel
                WHERE product_template_id=%s;
            """, (product.id,))

            for category_id in list(dict.fromkeys(category_ids)):
                cr.execute("""
                    INSERT INTO product_template_product_public_category_slug_rel(product_template_id, product_public_category_id)
                    VALUES(%s, %s);
                """, (product.id, category_id,))

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
                    slug_name = slugify(product.name or '').strip().strip('-')
                    product.website_slug = f'{prefix}/{slug_name}-{product.id}'

    @api.depends('product_variant_ids', 'product_variant_id', 'attribute_line_ids')
    def _compute_variant_attribute_value_ids(self):
        """
        Used to filter attribute values on the website.
        This method computes a list of attribute values from variants of published products.
        This will ensure that the available attribute values on the website filtering will return results.
        By default, Odoo only shows attributes that will return results but doesn't consider that a particular
        attribute value may not have a variant.
        """
        for product in self:
            variants = product.product_variant_ids
            attribute_values = variants.\
                mapped('product_template_attribute_value_ids').\
                mapped('product_attribute_value_id')
            attribute_values += variants.\
                mapped('valid_product_template_attribute_line_ids').\
                mapped('value_ids')
            product.variant_attribute_value_ids = [(6, 0, attribute_values.ids)]

    def _compute_recent_sales_count(self):
        self = self.filtered(lambda p: not isinstance(p.id, models.NewId))

        lookback_days = int(self.env['ir.config_parameter'].sudo().get_param('vsf_recent_sales_count_days', 30))
        date_days_ago = fields.Datetime.now() - timedelta(days=lookback_days)
        done_states = self.env['sale.report'].sudo()._get_done_states()
        domain = [
            ('state', 'in', done_states),
            ('date', '>=', date_days_ago),
        ]

        sale_groups = self.env['sale.report'].sudo().read_group(
            domain,
            ['product_id', 'product_uom_qty'],
            ['product_id'],
        )
        sale_count_map = {group['product_id'][0]: group['product_uom_qty'] for group in sale_groups}

        values = []

        for product in self:
            if product.detailed_type in ['product', 'consu']:
                product_id = product.product_variant_id.id
                sales_count = sale_count_map.get(product_id, 0)
                sales_count = float_round(sales_count, precision_rounding=product.uom_id.rounding)
                recent_sales_count = sales_count + product.recent_sales_count_increment
            else:
                recent_sales_count = 0

            values.append((recent_sales_count, product.id))

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

    variant_attribute_value_ids = fields.Many2many('product.attribute.value',
                                                   'product_template_variant_product_attribute_value_rel',
                                                   compute='_compute_variant_attribute_value_ids',
                                                   store=True, readonly=True)
    website_slug = fields.Char('Website Slug', compute='_compute_website_slug', store=True, readonly=True,
                               translate=True)
    public_categ_slug_ids = fields.Many2many('product.public.category',
                                             'product_template_product_public_category_slug_rel',
                                             compute='_compute_public_categ_slug_ids',
                                             store=True, readonly=True)
    recent_sales_count = fields.Float('Recent Sales Count', compute='_compute_recent_sales_count', store=True,
                                      readonly=True)
    recent_sales_count_increment = fields.Integer('Recent Sales Count Increment', default=0, required=True)
    frequently_bought_together_ids = fields.One2many('product.template.fbt', 'product_id', 'Frequently Bought Together',
                                                     readonly=True)
    product_tmpl_redis_stock_ids = fields.One2many('product.template.redis_stock', 'product_id', 'Redis Stock',
                                                   readonly=True)
    published_datetime = fields.Datetime('Published On', help='Datetime when the product was published', readonly=True,
                                         copy=False)
    published_hours = fields.Integer('Hours Published', compute='_compute_published_hours',
                                     help='Total hours the product has been published', readonly=True)
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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('website_published'):
                vals['published_datetime'] = datetime.now()
        return super(ProductTemplate, self).create(vals_list)

    def write(self, vals):
        if 'website_published' in vals:
            for product in self:
                if vals['website_published'] and not product.website_published and not product.published_datetime:
                    vals['published_datetime'] = datetime.now()

        res = super(ProductTemplate, self).write(vals)
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return res

    def unlink(self):
        self.env['invalidate.cache'].create_invalidate_cache(self._name, self.ids)
        return super(ProductTemplate, self).unlink()

    def _get_combination_info(self, combination=False, product_id=False, add_qty=1, parent_combination=False,
                              only_template=False):
        """ Add discount value and percentage based """
        combination_info = super(ProductTemplate, self)._get_combination_info(
            combination=combination, product_id=product_id, add_qty=add_qty, parent_combination=parent_combination,
            only_template=only_template)

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
        lookback_days = int(self.env['ir.config_parameter'].sudo().get_param('vsf_recent_sales_count_days', 30))
        date_days_ago = fields.Datetime.now() - timedelta(days=lookback_days)
        done_states = self.env['sale.report'].sudo()._get_done_states()
        domain = [
            ('state', 'in', done_states),
            ('date', '>=', date_days_ago),
        ]
        sale_groups = self.env['sale.report'].search(domain)

        order_to_products = defaultdict(list)
        for sale_group in sale_groups:
            if sale_group.product_id.detailed_type in ['product', 'consu']:
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


class ProductProduct(models.Model):
    _inherit = 'product.product'

    product_redis_stock_ids = fields.One2many('product.product.redis_stock', 'product_id', 'Redis Stock', readonly=True)
    has_stock = fields.Boolean(string='Has Stock', compute='_compute_has_stock', search='_search_has_stock',
                               store=False)

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

    def _compute_json_ld(self):
        env = self.env
        website = env['website'].get_current_website()
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base_url and base_url[-1:] == '/':
            base_url = base_url[:-1]

        website_domain = website.domain or ''
        if website_domain and website_domain[-1:] == '/':
            website_domain = website_domain[:-1]

        for product in self:
            # Get list of images
            images = list()
            if product.image_1920:
                images.append(f'{base_url}/web/image/product.product/{product.id}/image_1920')

            json_ld = {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": product.display_name,
                "image": images,
                "offers": {
                    "@type": "Offer",
                    "url": f"{website_domain}{product.website_slug}",
                    "priceCurrency": product.currency_id.name,
                    "price": product.list_price,
                    "itemCondition": "https://schema.org/NewCondition",
                    "availability": "https://schema.org/InStock",
                    "seller": {
                        "@type": "Organization",
                        "name": website and website.display_name or product.env.user.company_id.display_name
                    }
                }
            }

            if product.description_sale:
                json_ld.update({"description": product.description_sale})

            if product.default_code:
                json_ld.update({"sku": product.default_code})

            product.json_ld = json.dumps(json_ld)

    @api.model
    def _update_dirty_products_stock_redis(self):
        redis_client = self.env['website']._redis_connect()
        dirty_keys = [key for key in redis_client.scan_iter('stock:product-is-dirty-*')]
        product_ids = [int(redis_client.get(dirty_key)) for dirty_key in dirty_keys]
        products = self.search([('id', 'in', product_ids)])

        products._update_products_stock_redis(redis_client)

        for dirty_key in dirty_keys:
            redis_client.delete(dirty_key)

    @api.model
    def _update_all_products_stock_redis(self):
        redis_client = self.env['website']._redis_connect()
        products = self.search([])
        products._update_products_stock_redis(redis_client)

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

        pipe.execute()

        self.env['product.product.redis_stock'].bulk_update_redis_stock(product_stock_values)
        self.env['product.template.redis_stock'].bulk_update_redis_stock(template_stock_values)


class ProductStockRedis(models.AbstractModel):
    _name = 'product.redis_stock'

    website_id = fields.Many2one('website', 'Website', required=True)
    quantity = fields.Float('Quantity', digits='Product Unit of Measure', required=True)

    @api.model
    def bulk_update_redis_stock(self, values):
        """
        Efficiently upsert multiple redis stock records.

        :param values: list of tuples (product_id, website_id, quantity)
        :param table_name: str, target PostgreSQL table name
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

    product_id = fields.Many2one('product.product', 'Product', required=True)

    _sql_constraints = [
        ('unique_product_website', 'unique(product_id, website_id)', 'Product and Website must be unique!')
    ]


class ProductTemplateRedisStock(models.Model):
    _name = 'product.template.redis_stock'
    _inherit = 'product.redis_stock'

    product_id = fields.Many2one('product.template', 'Product', required=True)

    _sql_constraints = [
        ('unique_template_website', 'unique(product_id, website_id)', 'Template and Website must be unique!')
    ]


class ProductPublicCategory(models.Model):
    _inherit = 'product.public.category'

    def _compute_json_ld(self):
        website = self.env['website'].get_current_website()

        website_domain = website.domain or ''
        if website_domain and website_domain[-1:] == '/':
            website_domain = website_domain[:-1]

        for category in self:
            json_ld = {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "url": f'{website_domain}{category.website_slug}',
                "name": category.display_name,
            }

            category.json_ld = json.dumps(json_ld)

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
