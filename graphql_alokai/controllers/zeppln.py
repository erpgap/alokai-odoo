# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import hmac
import logging
import requests
from urllib.parse import urljoin

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ZepplnController(http.Controller):

    @http.route('/zeppln/launch', type='http', auth='user', csrf=False, methods=['GET'])
    def launch(self, website_id=None, odoo_url=None, zeppln_url=None, **kwargs):
        website = request.env['website'].sudo().browse(int(website_id or 0)).exists()
        if not website or not website.alokai_saas_token:
            return request.not_found()
        saas_base_url = (request.env['ir.config_parameter'].sudo().get_param('graphql_alokai.saas_base_url', 'https://www.zeppln.com') or 'https://www.zeppln.com').rstrip('/')
        if saas_base_url == 'https://zeppln.erpgap.com':
            saas_base_url = 'https://www.zeppln.com'
        target = saas_base_url + '/instance/launch'
        try:
            response = requests.post(target, data={
                'instance': odoo_url or website.domain,
                'odooToken': website.alokai_saas_token,
            }, allow_redirects=False, timeout=20)
        except requests.RequestException:
            return request.make_json_response({'error': 'Zeppln is unavailable.'}, status=502)
        if response.status_code not in (301, 302, 303, 307, 308) or not response.headers.get('Location'):
            return request.make_json_response({'error': 'Zeppln rejected the launch request.'}, status=502)
        # Zeppln normally returns an absolute Location. Resolve relative
        # redirects defensively so the browser does not stay on the Odoo host.
        return request.redirect(urljoin(target, response.headers['Location']))

    @http.route('/zeppln/instance-ready', type='http', auth='public', csrf=False, methods=['POST'])
    def instance_ready(self, **kwargs):
        """Called by the Zeppln storefront when it starts (NUXT_ZEPPLN_INIT),
        with the website Zeppln token (``Authorization: Bearer``) and the
        provisioned instance URL (JSON body ``{"instance_url": ...}``).

        The first call sets the domain and sends the storefront every stock
        and slug. Later ones - every restart - change nothing. The token stays:
        it signs what Odoo sends the storefront from then on.
        """
        auth = request.httprequest.headers.get('Authorization', '')
        token = auth[len('Bearer '):].strip() if auth.startswith('Bearer ') else ''
        website = request.env['website'].sudo().search([('alokai_saas_token', '!=', False)], limit=1)
        if not token or not website or not hmac.compare_digest(token, website.alokai_saas_token):
            return request.make_json_response({'error': 'Invalid token'}, status=401)

        try:
            data = request.get_json_data()
        except ValueError:
            data = None
        domain = isinstance(data, dict) and (data.get('instance_url') or data.get('domain'))
        if not domain or not isinstance(domain, str):
            return request.make_json_response({'error': 'Missing domain'}, status=400)

        if website.alokai_domain:
            return request.make_json_response({'status': 'ignored', 'domain': website.alokai_domain})

        website.alokai_domain = domain.strip().rstrip('/')
        # The storefront starts with an empty Redis: send it everything.
        request.env['product.product'].sudo()._update_all_products_stock_redis()
        request.env['website'].sudo()._update_all_slugs_redis()
        _logger.info("Zeppln instance reported for website %s: %s", website.id, website.alokai_domain)
        return request.make_json_response({'status': 'initialized', 'domain': website.alokai_domain})
