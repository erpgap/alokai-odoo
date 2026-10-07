# Copyright 2026 ERPGAP
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_SEO_FIELDS = ('website_meta_title', 'website_meta_description')


def migrate(cr, version):
    """Move the homepage's SEO from the website record onto its CMS page.

    Until now the homepage was the one page whose meta title and description
    were stored on `website` rather than on its own `alokai.website.page`,
    which meant a special case in the model, a `source` flag on the API and a
    sudo write for editors who are not website admins.

    Copied rather than moved: the website fields keep their values, so an
    install that is rolled back still renders what it did before. Nothing is
    overwritten either - a homepage that already has its own tags is left
    alone, because those are the newer ones.
    """
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    Page = env['alokai.website.page'].sudo()

    homepages = Page.search([('is_system', '=', True), ('url', '=', '/')])
    for page in homepages:
        website = page.website_id or env['website'].sudo().search([], limit=1)
        if not website:
            continue

        for field_name in _SEO_FIELDS:
            # Per language, because these are translated: taking the plain
            # value would collapse every translation into the base language.
            stored = website._fields[field_name]._get_stored_translations(website)
            if not stored:
                continue
            if page._fields[field_name]._get_stored_translations(page):
                continue
            page.update_field_translations(field_name, stored)

        if website.website_meta_img and not page.website_meta_img:
            page.website_meta_img = website.website_meta_img

        _logger.info(
            'graphql_alokai: homepage SEO copied from website %s to page %s',
            website.id, page.id,
        )
