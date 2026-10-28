# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import hmac
import logging
import html

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ZepplnController(http.Controller):

    @http.route('/zeppln/launch', type='http', auth='user', csrf=False, methods=['GET'])
    def launch(self, website_id=None, odoo_url=None, zeppln_url=None, **kwargs):
        website = request.env['website'].sudo().browse(int(website_id or 0)).exists()
        if not website or not website.alokai_saas_token:
            return request.not_found()
        target = (zeppln_url or 'https://www.zeppln.com').rstrip('/') + '/instance/launch'
        values = {
            'instance': odoo_url or website.domain,
            'odooToken': website.alokai_saas_token,
        }
        inputs = ''.join('<input type="hidden" name="%s" value="%s">' % (html.escape(key), html.escape(str(value or ''))) for key, value in values.items())
        return request.make_response('<!doctype html><html><body><form id="launch" method="post" action="%s">%s</form><script>document.getElementById("launch").submit()</script></body></html>' % (html.escape(target), inputs), headers=[('Content-Type', 'text/html; charset=utf-8')])

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
