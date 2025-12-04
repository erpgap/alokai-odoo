# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import os
import json
import logging
import hashlib
from graphql import parse, print_ast

from odoo import http
from odoo.addons.web.controllers.binary import Binary
from odoo.addons.graphql_base import GraphQLControllerMixin
from odoo.http import request, Response
from odoo.tools.func import lazy
from urllib.parse import urlparse
from werkzeug.exceptions import Forbidden


_logger = logging.getLogger(__name__)


from ..graphql.registry import build_alokai_schema


class AlokaiBinary(Binary):
    @http.route(['/web/image',
                 '/web/image/<string:xmlid>',
                 '/web/image/<string:xmlid>/<string:filename>',
                 '/web/image/<string:xmlid>/<int:width>x<int:height>',
                 '/web/image/<string:xmlid>/<int:width>x<int:height>/<string:filename>',
                 '/web/image/<string:model>/<int:id>/<string:field>',
                 '/web/image/<string:model>/<int:id>/<string:field>/<string:filename>',
                 '/web/image/<string:model>/<int:id>/<string:field>/<int:width>x<int:height>',
                 '/web/image/<string:model>/<int:id>/<string:field>/<int:width>x<int:height>/<string:filename>',
                 '/web/image/<int:id>',
                 '/web/image/<int:id>/<string:filename>',
                 '/web/image/<int:id>/<int:width>x<int:height>',
                 '/web/image/<int:id>/<int:width>x<int:height>/<string:filename>',
                 '/web/image/<int:id>-<string:unique>',
                 '/web/image/<int:id>-<string:unique>/<string:filename>',
                 '/web/image/<int:id>-<string:unique>/<int:width>x<int:height>',
                 '/web/image/<int:id>-<string:unique>/<int:width>x<int:height>/<string:filename>'], type='http',
                auth="public")
    def content_image(self, xmlid=None, model='ir.attachment', id=None, field='raw',
                    filename_field='name', filename=None, mimetype=None, unique=False,
                    download=False, width=0, height=0, crop=False, access_token=None,
                    nocache=False):
        """ Validate width and height """
        try:
            ICP = request.env['ir.config_parameter'].sudo()
            alokai_image_resize_limit = int(ICP.get_param('alokai_image_resize_limit', 1920))
            
            if width > alokai_image_resize_limit or height > alokai_image_resize_limit:
                return request.not_found()
        except Exception:
            return request.not_found()

        return super(AlokaiBinary, self).content_image(
            xmlid=xmlid, model=model, id=id, field=field, filename_field=filename_field, filename=filename,
            mimetype=mimetype, unique=unique, download=download, width=width, height=height, crop=crop,
            access_token=access_token, nocache=nocache)


class GraphQLController(http.Controller, GraphQLControllerMixin):

    _graphql_schema = False

    def __init__(self):
        super(GraphQLController, self).__init__()
        self._graphql_schema = build_alokai_schema().graphql_schema

    def _process_request(self, schema, data):
        # Set the alokai_debug_mode value that exist in the settings
        env = http.request.env

        ICP = env['ir.config_parameter'].sudo()
        if ICP.get_param('alokai_debug_mode', False):
            request = http.request.httprequest

            # Headers
            headers = request.headers.environ and dict(request.headers.environ) or {}
            headers = json.dumps(headers, indent=2)

            # Query / Mutation
            try:
                query = parse(data.get('query') or '')
                query = print_ast(query)
            except Exception:
                query = data.get('query') or ''

            variables = data.get('variables') or '{}'
            if isinstance(variables, str):
                variables = json.loads(variables)
            variables = json.dumps(variables, indent=2)

            query_hash = hashlib.sha256((query + variables).encode('utf-8')).hexdigest()

            WebsiteGraphqlHash = env['website.graphql.hash'].sudo()
            if not WebsiteGraphqlHash.search([('hash', '=', query_hash)], limit=1):
                # First time seeing this hash
                WebsiteGraphqlHash.create({'hash': query_hash})
            else:
                WebsiteQueryNotCached = env['website.graphql.not_cached'].sudo()

                # Seen before, log not cached
                not_cached_hash = WebsiteQueryNotCached.search([('hash', '=', query_hash)], limit=1)
                if not_cached_hash:
                    not_cached_hash.write({
                        'count': not_cached_hash.count + 1,
                    })
                else:
                    WebsiteQueryNotCached.create({
                        'hash': query_hash,
                        'query': query,
                        'variables': variables,
                        'count': 1,
                    })

            try:
                def log_section(title, content):
                    separator = '-' * 100
                    _logger.info(separator)
                    _logger.info(f'{title:^100}')  # Centered title in chars width
                    _logger.info(separator)
                    if content:
                        _logger.info(content)

                log_section(f'GRAPHQL DEBUG: {query_hash}', '')
                log_section('HEADERS', headers)
                log_section('QUERY / MUTATION', query)
                log_section('VARIABLES', f"{variables}")
            except:
                pass
        return super(GraphQLController, self)._process_request(schema, data)

    def _set_website_context(self):
        """Set website context based on http_request_host header."""
        website = None
        try:
            request_host = request.httprequest.headers.environ.get('HTTP_REQUEST_HOST')
            if not request_host.startswith(('http://', 'https://')):
                request_host = f'https://{request_host}'
            website = request.env['website'].search([('domain', '=', request_host)], limit=1)
        except:
            pass

        if not website:
            website = request.env['website'].search([], limit=1)

        request.update_context(
            website_id=website.id,
            lang=website.default_lang_id.code,
        )
        request.website = website

        request_uid = request.env.uid
        website_uid = website.sudo().user_id.id

        if request_uid != website_uid \
                and request.env['res.users'].sudo().browse(request_uid).has_group('base.group_public'):
            request.update_env(user=website_uid)

        # Initialize cart and pricelist for Odoo v19 compatibility
        if not hasattr(request, 'cart'):
            request.cart = lazy(website._get_and_cache_current_cart)
        if not hasattr(request, 'pricelist'):
            request.pricelist = lazy(website._get_and_cache_current_pricelist)

    # The GraphiQL route, providing an IDE for developers
    @http.route(["/graphiql/alokai", "/graphiql/vsf"], auth="public")
    def graphiql(self, **kwargs):
        self._set_website_context()

        # If debug mode is active, we can access with public user which is useful for testing
        ICP = http.request.env['ir.config_parameter'].sudo()
        alokai_debug_mode = ICP.get_param('alokai_debug_mode', False)
        if not alokai_debug_mode:
            # Check if the current user belongs to the internal user group
            if not request.env.user.has_group('base.group_user'):
                raise Forbidden()

        return self._handle_graphiql_request(self._graphql_schema)

    # The graphql route, for applications.
    # Note csrf=False: you may want to apply extra security
    # (such as origin restrictions) to this route.
    @http.route(["/graphql/alokai", "/graphql/vsf"], auth="public", csrf=False)
    def graphql(self, **kwargs):
        self._set_website_context()
        return self._handle_graphql_request(self._graphql_schema)

    @http.route(['/alokai/categories', '/vsf/categories'], type='http', auth='public', csrf=False)
    def alokai_categories(self):
        self._set_website_context()
        website = request.env['website'].get_current_website()

        categories = []

        if website.default_lang_id:
            lang_code = website.default_lang_id.code
            domain = [('website_slug', '!=', False)]

            for category in request.env['product.public.category'].sudo().search(domain):
                category = category.with_context(lang=lang_code)
                categories.append(category.website_slug)

        return Response(
            json.dumps(categories),
            headers={'Content-Type': 'application/json'},
        )

    @http.route(['/alokai/products', '/vsf/products'], type='http', auth='public', csrf=False)
    def alokai_products(self):
        self._set_website_context()
        website = request.env['website'].get_current_website()

        products = []

        if website.default_lang_id:
            lang_code = website.default_lang_id.code
            domain = [('is_published', '=', True), ('website_slug', '!=', False)]

            for product in request.env['product.template'].sudo().search(domain):
                product = product.with_context(lang=lang_code)

                url_parsed = urlparse(product.website_slug)
                name = os.path.basename(url_parsed.path)
                path = product.website_slug.replace(name, '')

                products.append({
                    'name': name,
                    'path': '{}:slug'.format(path),
                })

        return Response(
            json.dumps(products),
            headers={'Content-Type': 'application/json'},
        )

    @http.route(['/alokai/redirects', '/vsf/redirects'], type='http', auth='public', csrf=False)
    def alokai_redirects(self):
        redirects = []

        for redirect in request.env['website.rewrite'].sudo().search([]):
            redirects.append({
                'from': redirect.url_from,
                'to': redirect.url_to,
            })

        return Response(
            json.dumps(redirects),
            headers={'Content-Type': 'application/json'},
        )

    @http.route('/checkout-redirect', type='http', auth='none', csrf=False)
    def checkout_redirect(self, access_token=None, **kwargs):
        """Replace the Odoo session ID with the provided one, used to redirect Alokai to Odoo checkout"""
        if access_token:
            redis_client = request.env['website']._redis_connect()
            session_id = redis_client.get(access_token)
            if session_id:
                try:
                    session = http.root.session_store.get(session_id)
                    if session and session.get('sale_order_id'):
                        sale_order_id = session.get('sale_order_id')
                        request.session = session
                        request.session.sid = session_id
                        request.session.modified = True
                        request.session.sale_order_id = sale_order_id
                        SaleOrder = request.env['sale.order'].sudo()
                        order_sudo = SaleOrder.browse(sale_order_id).exists()
                        request.session.website_sale_cart_quantity = order_sudo.cart_quantity
                        response = request.env['ir.http']._dispatch('/shop/checkout', request.httprequest.method)
                        response.set_cookie(
                            'session_id',
                            session_id,
                            path='/',
                            httponly=True,
                            samesite='None',
                            secure=True
                        )
                        return response
                except:
                    pass

        return request.redirect('/shop/checkout')
