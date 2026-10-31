# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import hmac
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ZepplnController(http.Controller):

    @http.route('/zeppln/instance-ready', type='http', auth='public', csrf=False, methods=['POST'])
    def instance_ready(self, **kwargs):
        """Called by Zeppln once it has created an instance, with the token
        Settings' Create button gave its website (``Authorization: Bearer``)
        and the instance URL (JSON body ``{"instance_url": ...}``). Odoo cannot
        tell when that is otherwise: it takes long after the button's request.

        Odoo writes the URL on the website and queues every stock and slug;
        the dirty cron sends them, retrying each minute until the storefront
        is up. The same URL again changes nothing; a new one replaces it and
        queues everything again. The token stays: it signs what Odoo sends the
        storefront from then on.
        """
        auth = request.httprequest.headers.get('Authorization', '')
        token = auth[len('Bearer '):].strip() if auth.startswith('Bearer ') else ''
        website = request.env['website'].sudo().search([('alokai_saas_token', '=', token)], limit=1) if token else request.env['website']
        if not token or not website or not hmac.compare_digest(token, website.alokai_saas_token):
            return request.make_json_response({'error': 'Invalid token'}, status=401)

        try:
            data = request.get_json_data()
        except ValueError:
            data = None
        domain = isinstance(data, dict) and (data.get('instance_url') or data.get('domain'))
        if not domain or not isinstance(domain, str):
            return request.make_json_response({'error': 'Missing domain'}, status=400)

        domain = domain.strip().rstrip('/')
        if website.alokai_domain == domain:
            return request.make_json_response({'status': 'unchanged', 'domain': website.alokai_domain})

        website.alokai_domain = domain
        # The storefront starts with an empty Redis: send it everything.
        request.env['product.product'].sudo()._update_all_products_stock_redis()
        request.env['website'].sudo()._update_all_slugs_redis()
        _logger.info("Zeppln instance reported for website %s: %s", website.id, website.alokai_domain)
        return request.make_json_response({'status': 'initialized', 'domain': website.alokai_domain})
