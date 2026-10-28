# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import logging

_logger = logging.getLogger(__name__)

# Return URL column: (the column that held only its path, the default path).
PAYMENT_RETURN_COLUMNS = {
    'alokai_payment_success_return_url': ('alokai_payment_success_path', '/checkout/thank-you'),
    'alokai_payment_error_return_url': ('alokai_payment_error_path', '/checkout/payment-fail'),
}

# The storefront base URL, as website._alokai_domain() builds it.
BASE_URL = """rtrim(coalesce(
    nullif(alokai_domain, ''), nullif(domain, ''),
    (SELECT value FROM ir_config_parameter WHERE key = 'web.base.url'), ''), '/')"""


def _column_type(cr, column):
    cr.execute("""
        SELECT data_type FROM information_schema.columns
         WHERE table_name = 'website' AND column_name = %s
    """, (column,))
    row = cr.fetchone()
    return row and row[0]


def migrate(cr, version):
    """The payment return URLs are no longer translatable, and follow the
    storefront domain.

    Odoo keeps a column translated (jsonb) for as long as ir_model_fields says
    the field was, so it is converted here, at the end, keeping the English
    value. A database that briefly stored only the paths gets its URLs back
    from them, and a value that is not a URL - the old 'Dummy' default -
    becomes the default page on the storefront.
    """
    for column, (path_column, default_path) in PAYMENT_RETURN_COLUMNS.items():
        if _column_type(cr, column) == 'jsonb':
            cr.execute(f"ALTER TABLE website ALTER COLUMN {column} TYPE varchar USING {column}->>'en_US'")
        if _column_type(cr, path_column):
            cr.execute(f"""
                UPDATE website SET {column} = {BASE_URL} || coalesce(nullif({path_column}, ''), %s)
            """, (default_path,))
            cr.execute(f"ALTER TABLE website DROP COLUMN {path_column}")
        cr.execute(f"""
            UPDATE website SET {column} = {BASE_URL} || %s
             WHERE {column} IS NULL OR {column} NOT LIKE '%%://%%'
        """, (default_path,))
    cr.execute("""
        UPDATE ir_model_fields SET translate = NULL
         WHERE model = 'website' AND name IN %s
    """, (tuple(PAYMENT_RETURN_COLUMNS),))
    _logger.info('graphql_alokai: payment return URLs now follow the storefront domain')
