# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Lower the storefront image quality from the shipped 100 to 80.

    Resized images are re-encoded as WebP at this quality. At 100 WebP is
    close to lossless: a 370px product image was 30-45KB, against 8-14KB at
    80 with no difference a shopper can see. Only the untouched default is
    changed; a value someone chose is kept.
    """
    cr.execute("""
        UPDATE ir_config_parameter
           SET value = '80'
         WHERE key = 'alokai_image_quality'
           AND value = '100'
    """)
    if cr.rowcount:
        _logger.info('graphql_alokai: storefront image quality lowered from 100 to 80')
