# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import json
from odoo.osv import expression
from collections import defaultdict
from datetime import timedelta
from odoo import models, fields, api, _
from odoo.tools.float_utils import float_round
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.model
    def _graphql_get_search_order(self, sort):
        sorting = ''
        for field, val in sort.items():
            if sorting:
                sorting += ', '
            if field == 'price':
                sorting += 'list_price %s' % val.value
            elif field == 'popular':
                sorting += 'recent_sales_count %s' % val.value
            elif field == 'newest':
                sorting += 'create_date %s' % val.value
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
            domains.append([
                ('product_tmpl_redis_stock_ids.quantity', '>', 0),
                ('product_tmpl_redis_stock_ids.website_id', '=', website.id)
            ])

        if search:
            for srch in search.split(" "):
                domains.append([
                    '|', '|', ('name', 'ilike', srch), ('description_sale', 'like', srch), ('default_code', 'like', srch)])

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

        for product in self:
            # Get list of images
            images = list()
            if product.image_1920:
                images.append(f'{base_url}/web/image/product.product/{product.id}/image')

            json_ld = {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": product.display_name,
                "image": images,
                "offers": {
                    "@type": "Offer",
                    "url": f"{website.domain or ''}/product/{self.env['ir.http']._slug(product)}",
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
                    slug_name = self.env['ir.http']._slugify(product.name or '').strip().strip('-')
                    product.website_slug = f'{prefix}/{slug_name}-{product.id}'

    @api.depends('product_variant_ids')
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
        lookback_days = int(self.env['ir.config_parameter'].sudo().get_param('alokai_recent_sales_count_days', 30))
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
        # TODO: check why product id is False in sale.report
        sale_count_map = {group['product_id'][0]: group['product_uom_qty'] for group in sale_groups if group and group.get('product_uom_qty')}

        for product in self:
            product_id = product.product_variant_id.id
            sales_count = sale_count_map.get(product_id, 0)
            sales_count = float_round(sales_count, precision_rounding=product.uom_id.rounding)
            product.recent_sales_count = sales_count + product.recent_sales_count_increment

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

    def write(self, vals):
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
        ProductTemplateFBT = self.env['product.template.fbt']

        ProductTemplateFBT.search([]).unlink()
        sale_groups = self.env['sale.report'].search([])

        order_to_products = defaultdict(list)
        for sale_group in sale_groups:
            order_id = sale_group.order_reference
            product_id = sale_group.product_id.product_tmpl_id.id
            qty = sale_group.product_uom_qty
            order_to_products[order_id].append((product_id, qty))

        product_relations = defaultdict(lambda: defaultdict(float))
        for order, products in order_to_products.items():
            # For each order, track pairs of products and add their quantities
            for i in range(len(products)):
                for j in range(i + 1, len(products)):
                    product_a, qty_a = products[i]
                    product_b, qty_b = products[j]
                    # Add the quantities of the products to each other (since it's symmetric)
                    product_relations[product_a][product_b] += min(qty_a, qty_b)
                    product_relations[product_b][product_a] += min(qty_a, qty_b)

        for product_id, related_products in product_relations.items():
            related_product_pairs = sorted(related_products.items(), key=lambda p: -p[1])

            for related_product_id, qty in related_product_pairs:
                ProductTemplateFBT.create({
                    'product_id': product_id,
                    'related_product_id': related_product_id,
                    'qty': qty,
                })

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

    def _compute_json_ld(self):
        env = self.env
        website = env['website'].get_current_website()
        base_url = env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        if base_url and base_url[-1:] == '/':
            base_url = base_url[:-1]

        for product in self:
            # Get list of images
            images = list()
            if product.image_1920:
                images.append(f'{base_url}/web/image/product.product/{product.id}/image')

            json_ld = {
                "@context": "https://schema.org/",
                "@type": "Product",
                "name": product.display_name,
                "image": images,
                "offers": {
                    "@type": "Offer",
                    "url": f"{website.domain or ''}/product/{slug(product)}",
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

    def _update_dirty_products_stock_redis(self):
        redis_client = self.env['website']._redis_connect()
        dirty_keys = [key for key in redis_client.scan_iter('product-stock-is-dirty-*')]
        product_ids = [int(redis_client.get(dirty_key)) for dirty_key in dirty_keys]
        products = self.search([('id', 'in', product_ids)])

        self._update_products_stock_redis(redis_client, products)

        for dirty_key in dirty_keys:
            redis_client.delete(dirty_key)

    def _update_all_products_stock_redis(self):
        redis_client = self.env['website']._redis_connect()
        products = self.search([])
        self._update_products_stock_redis(redis_client, products)

    def _update_products_stock_redis(self, redis_client, products):
        if products:
            product_tmpls = products.mapped('product_tmpl_id')
            websites = self.env['website'].search([])

            ProductProductRedisStock = self.env['product.product.redis_stock']
            ProductTemplateRedisStock = self.env['product.template.redis_stock']
            pipe = redis_client.pipeline()

            for product in products:
                data = {}
                for website in websites:
                    ProductProductRedisStock.create_redis_stock(product.id, website.id, product.free_qty)
                    data.update({website.id: self.free_qty})
                pipe.set(f'product-stock-{product.id}', json.dumps(data))

            for product_tmpl in product_tmpls:
                for website in websites:
                    free_qty = sum(product_tmpl.product_variant_ids.mapped('free_qty'))
                    ProductTemplateRedisStock.create_redis_stock(product_tmpl.id, website.id, free_qty)

            pipe.execute()


class ProductStockRedis(models.AbstractModel):
    _name = 'product.redis_stock'

    website_id = fields.Many2one('website', 'Website', required=True)
    quantity = fields.Float('Quantity', digits='Product Unit of Measure', required=True)

    @api.model
    def create_redis_stock(self, product_id, website_id, quantity):
        line = self.search([('product_id', '=', product_id), ('website_id', '=', website_id)])
        if line:
            line.quantity = quantity
        else:
            self.create({
                'product_id': product_id,
                'website_id': website_id,
                'quantity': quantity,
            })


class ProductProductRedisStock(models.Model):
    _name = 'product.product.redis_stock'
    _inherit = 'product.redis_stock'

    product_id = fields.Many2one('product.product', 'Product', required=True)


class ProductTemplateRedisStock(models.Model):
    _name = 'product.template.redis_stock'
    _inherit = 'product.redis_stock'

    product_id = fields.Many2one('product.template', 'Product', required=True)


class ProductPublicCategory(models.Model):
    _inherit = 'product.public.category'

    def _compute_json_ld(self):
        website = self.env['website'].get_current_website()
        base_url = website.domain or ''
        if base_url and base_url[-1] == '/':
            base_url = base_url[:-1]

        for category in self:
            json_ld = {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "url": f'{base_url}{category.website_slug}',
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
