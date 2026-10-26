# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import hashlib
import hmac
import json
import logging
import time

import requests

from odoo import models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AlokaiCacheMixin(models.AbstractModel):
    """Clear records' pages from the Alokai cache, on request.

    The storefront caches a product or category page - its HTML and the
    GraphQL data behind it - for NUXT_SWR_CACHE_TIME. Clearing is manual: a
    merchant picks the records and runs the action, rather than every write
    calling the storefront.
    """
    _name = 'alokai.cache.mixin'
    _description = 'Alokai Cache'

    # The storefront route that clears this kind of page:
    # /api/internal/cache/invalidate/<route>
    _alokai_cache_route = None

    def _alokai_cache_targets(self):
        """``{(storefront base URL, page path)}`` for these records: their slug
        in every installed language (the storefront has no language prefix,
        so each is a page of its own), on every website showing them."""
        langs = [code for code, _name in self.env['res.lang'].get_installed()]
        all_websites = self.env['website'].sudo().search([])
        missing, targets = self.env['website'], set()
        for record in self:
            paths = {record.with_context(lang=lang).website_slug for lang in langs} - {False, None, ''}
            for website in record.website_id or all_websites:
                if not website.alokai_domain:
                    missing |= website
                    continue
                base_url = website.alokai_domain.rstrip('/')
                if '://' not in base_url:
                    base_url = 'https://' + base_url
                targets.update((base_url, path) for path in paths)
        if missing:
            raise UserError(_(
                'Set the Alokai Domain of these websites first, so Odoo knows '
                'which Alokai to clear: %s', ', '.join(missing.mapped('name'))))
        return targets

    def _alokai_post_cache_invalidation(self, session, key, secret, base_url, path):
        """One signed request, as the storefront's cacheInvalidationSecurity
        checks it: HMAC-SHA256 of METHOD:PATH:TIMESTAMP:CANONICAL_JSON with the
        signing secret, where canonical JSON has sorted keys and no
        whitespace."""
        route = f'/api/internal/cache/invalidate/{self._alokai_cache_route}'
        body = json.dumps({'url': path}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        timestamp = str(int(time.time() * 1000))
        signature = hmac.new(
            secret.encode(), f'POST:{route}:{timestamp}:{body}'.encode(), hashlib.sha256).hexdigest()
        response = session.post(base_url + route, data=body.encode(), timeout=10, headers={
            'Content-Type': 'application/json',
            'x-invalidation-key': key,
            'x-invalidation-timestamp': timestamp,
            'x-invalidation-signature': signature,
        })
        response.raise_for_status()

    def action_alokai_clear_cache(self):
        ICP = self.env['ir.config_parameter'].sudo()
        key = ICP.get_param('alokai_cache_invalidation_key')
        # Like the storefront: without a separate signing secret, the key signs.
        secret = ICP.get_param('alokai_cache_invalidation_signing_secret') or key
        if not key:
            raise UserError(_(
                'Set the Cache Invalidation Key in Settings > Website > Alokai '
                'first. It must match NUXT_CACHE_INVALIDATION_KEY on Alokai.'))

        targets = sorted(self._alokai_cache_targets())
        failures = []
        with requests.Session() as session:
            for base_url, path in targets:
                try:
                    self._alokai_post_cache_invalidation(session, key, secret, base_url, path)
                except requests.RequestException as error:
                    _logger.warning('Alokai cache: clearing %s%s failed: %s', base_url, path, error)
                    failures.append(f'{base_url}{path}: {error}')

        cleared = len(targets) - len(failures)
        if failures:
            message = _('Cleared %(cleared)s of %(total)s pages. Failed:\n%(failures)s',
                        cleared=cleared, total=len(targets), failures='\n'.join(failures[:10]))
        else:
            message = _('Cleared %(cleared)s pages from the Alokai cache.', cleared=cleared)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Alokai cache'),
                'message': message,
                'type': 'warning' if failures else 'success',
                'sticky': bool(failures),
            },
        }
