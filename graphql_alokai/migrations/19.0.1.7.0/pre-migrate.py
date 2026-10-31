# -*- coding: utf-8 -*-
# Copyright 2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

OLD, NEW = 'alokai_zeppln_dirty', 'alokai_sync_queue'
OLD_MODEL, NEW_MODEL = 'alokai.zeppln.dirty', 'alokai.sync.queue'


def migrate(cr, version):
    """alokai.zeppln.dirty is alokai.sync.queue: it queues stock and slugs
    for any storefront Odoo pushes to, not only Zeppln's. Renamed before the
    update, with the records describing it, so rows still queued are kept and
    the update does not create a second, empty table."""
    cr.execute("SELECT to_regclass(%s)", [OLD])
    if not cr.fetchone()[0]:
        return
    cr.execute(f"ALTER TABLE {OLD} RENAME TO {NEW}")
    cr.execute(f"ALTER SEQUENCE IF EXISTS {OLD}_id_seq RENAME TO {NEW}_id_seq")
    cr.execute(f"ALTER INDEX IF EXISTS {OLD}_pkey RENAME TO {NEW}_pkey")
    cr.execute("UPDATE ir_model SET model = %s WHERE model = %s", [NEW_MODEL, OLD_MODEL])
    cr.execute("UPDATE ir_model_fields SET model = %s WHERE model = %s", [NEW_MODEL, OLD_MODEL])
    cr.execute("UPDATE ir_model_data SET model = %s WHERE model = %s", [NEW_MODEL, OLD_MODEL])
    # The xml ids Odoo derives from the model's name: the model, its fields,
    # and the access rule (security/ir.model.access.csv).
    cr.execute("""
        UPDATE ir_model_data SET name = replace(name, %s, %s)
         WHERE module = 'graphql_alokai' AND name IN (%s, %s)
    """, [OLD, NEW, f'model_{OLD}', f'access_{OLD}_system'])
    cr.execute("""
        UPDATE ir_model_data SET name = replace(name, %s, %s)
         WHERE module = 'graphql_alokai' AND name LIKE %s
    """, [f'field_{OLD}__', f'field_{NEW}__', f'field_{OLD}\\_\\_%'])
