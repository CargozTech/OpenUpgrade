# Copyright 2025 ForgeFlow S.L. (https://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


def fill_stock_picking_type_default_locations(env):
    picking_types = env["stock.picking.type"].search(
        [("default_location_src_id", "=", False)]
    )
    picking_types._compute_default_location_src_id()
    picking_types = env["stock.picking.type"].search(
        [("default_location_dest_id", "=", False)]
    )
    picking_types._compute_default_location_dest_id()


def fix_picking_type_company_mismatch(env):
    """Fix company mismatch between stock.picking.type and their warehouses.
    Picking types should have the same company as their warehouse to prevent
    errors when computing default locations.
    """
    env.cr.execute("""
        UPDATE stock_picking_type spt
        SET company_id = wh.company_id
        FROM stock_warehouse wh
        WHERE spt.warehouse_id = wh.id
          AND spt.company_id != wh.company_id
    """)


def fix_sequence_company_mismatch(env):
    """Fix company mismatch between stock.picking.type and their sequences.
    This prevents errors when computing default locations where the picking type
    and its sequence belong to different companies.
    """
    env.cr.execute("""
        UPDATE ir_sequence seq
        SET company_id = spt.company_id
        FROM stock_picking_type spt
        WHERE spt.sequence_id = seq.id
          AND (spt.company_id != seq.company_id
               OR (spt.company_id IS NOT NULL AND seq.company_id IS NULL))
    """)



@openupgrade.migrate()
def migrate(env, version):
    fix_picking_type_company_mismatch(env)
    fix_sequence_company_mismatch(env)
    fill_stock_picking_type_default_locations(env)
