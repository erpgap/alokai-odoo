# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import hmac
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AlokaiSaas(http.Controller):

    @http.route('/alokai/saas/instance', type='http', auth='public', csrf=False, methods=['POST'])
    def saas_instance(self, **kwargs):
        """Called back by the SaaS once the Alokai instance exists, with the
        token of the website created for it (``Authorization: Bearer``) and
        the instance domain (JSON body ``{"domain": ...}``), which is only
        known at this point. The token works once."""
        auth = request.httprequest.headers.get('Authorization', '')
        token = auth[len('Bearer '):].strip() if auth.startswith('Bearer ') else ''
        website = request.env['website'].sudo().search([('alokai_saas_token', '!=', False)], limit=1)
        if not token or not website or not hmac.compare_digest(token, website.alokai_saas_token):
            return request.make_json_response({'error': 'Invalid token'}, status=401)

        try:
            data = request.get_json_data()
        except ValueError:
            data = None
        domain = isinstance(data, dict) and data.get('domain')
        if not domain or not isinstance(domain, str):
            return request.make_json_response({'error': 'Missing domain'}, status=400)

        # The token is single use: once the instance is reported, clearing it
        # keeps the website from being processed again.
        website.write({
            'alokai_domain': domain.strip().rstrip('/'),
            'alokai_saas_token': False,
        })
        ICP = request.env['ir.config_parameter'].sudo()
        ICP.set_param('graphql_alokai.saas_website_id', website.id)
        ICP.set_param('graphql_alokai.saas_instance_created', True)
        _logger.info("Alokai SaaS instance reported for website %s: %s", website.id, website.alokai_domain)
        return request.make_json_response({'website_id': website.id, 'domain': website.alokai_domain})
