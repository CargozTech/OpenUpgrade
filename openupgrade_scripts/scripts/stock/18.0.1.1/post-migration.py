# Copyright 2025 ForgeFlow S.L. (https://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade, openupgrade_180
import logging
_logger = logging.getLogger(__name__)


def convert_company_dependent(env):
    openupgrade_180.convert_company_dependent(
        env, "product.template", "property_stock_inventory"
    )
    openupgrade_180.convert_company_dependent(
        env, "product.template", "property_stock_production"
    )
    openupgrade_180.convert_company_dependent(env, "product.template", "responsible_id")
    openupgrade_180.convert_company_dependent(
        env, "res.partner", "property_stock_customer"
    )
    openupgrade_180.convert_company_dependent(
        env, "res.partner", "property_stock_supplier"
    )

def _fix_warehouse_related_companies(env):
    """
    Fix company_id mismatches for all warehouse-related records.
    This ensures locations, routes, and picking types match warehouse company.
    """
    all_warehouses = env["stock.warehouse"].with_context(active_test=False).search([])

    for wh in all_warehouses:
        if not wh.company_id:
            continue

        # Fix all warehouse locations
        location_fields = [
            ('view_location_id', wh.view_location_id),
            ('lot_stock_id', wh.lot_stock_id),
            ('wh_input_stock_loc_id', wh.wh_input_stock_loc_id),
            ('wh_qc_stock_loc_id', wh.wh_qc_stock_loc_id),
            ('wh_output_stock_loc_id', wh.wh_output_stock_loc_id),
            ('wh_pack_stock_loc_id', wh.wh_pack_stock_loc_id),
        ]

        for field_name, location in location_fields:
            if location and location.company_id and location.company_id != wh.company_id:
                location.sudo()._write({'company_id': wh.company_id.id})
                location.invalidate_recordset(['company_id'])

        # Fix all warehouse routes
        if wh.route_ids:
            for route in wh.route_ids:
                if route.company_id and route.company_id != wh.company_id:
                    route.sudo()._write({'company_id': wh.company_id.id})
                    route.invalidate_recordset(['company_id'])


def _create_default_new_types_for_all_warehouses(env):
    # method mainly based on _create_or_update_sequences_and_picking_types()
    all_warehouses = env["stock.warehouse"].with_context(active_test=False).search([])
    for wh in all_warehouses:
        sequence_data = wh._get_sequence_values()
        for field in ["qc_type_id", "store_type_id", "xdock_type_id"]:
            # choose the next available color for the operation types of this warehouse
            all_used_colors = [
                res["color"]
                for res in env["stock.picking.type"]
                .with_context(active_test=False)
                .search_read(
                    [("warehouse_id", "!=", False), ("color", "!=", False)],
                    ["color"],
                    order="color",
                )
            ]
            available_colors = [
                zef for zef in range(0, 12) if zef not in all_used_colors
            ]
            color = available_colors[0] if available_colors else 0
            # suit for each warehouse: reception, internal, pick, pack, ship
            max_sequence = (
                env["stock.picking.type"]
                .with_context(active_test=False)
                .search_read(
                    [("sequence", "!=", False)],
                    ["sequence"],
                    limit=1,
                    order="sequence desc",
                )
            )
            max_sequence = max_sequence and max_sequence[0]["sequence"] or 0
            values = wh._get_picking_type_update_values()[field]
            create_data, _ = wh._get_picking_type_create_values(max_sequence)
            values.update(create_data[field])
            sequence = env["ir.sequence"].create(sequence_data[field])
            values.update(
                warehouse_id=wh.id,
                color=color,
                sequence_id=sequence.id,
                sequence=max_sequence + 1,
                company_id=wh.company_id.id,
                active=wh.active,
            )
            # create picking type
            picking_type_id = env["stock.picking.type"].create(values).id
            # update picking type for warehouse using _write() to bypass company checks
            wh.sudo()._write({field: picking_type_id})
            wh.invalidate_recordset([field])


def _set_inter_company_locations(env):
    """See https://github.com/odoo/odoo/commit/08536d687880ca6d9ad5c37b639c0ad4c2599d74"""
    companies = env["res.company"].search([])
    if len(companies) > 1:
        inter_company_location = env.ref("stock.stock_location_inter_company")
        inactive = False
        if not inter_company_location.active:
            inactive = True
            inter_company_location.sudo().write({"active": True})
        for company in companies:
            company.sudo()._set_per_company_inter_company_locations(
                inter_company_location
            )
        if inactive:
            # we leave everything as it was
            inter_company_location.sudo().write({"active": False})

def _fix_uom_category_mismatches(env):
    """Align move and move line UoM with the product base UoM when UoM categories differ."""
    openupgrade.logged_query(
        env.cr,
        """
        WITH mismatches AS (
            SELECT sm.id AS move_id, pt.uom_id AS new_uom
            FROM stock_move sm
            JOIN product_product pp ON pp.id = sm.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom mu ON mu.id = sm.product_uom
            JOIN uom_uom pu ON pu.id = pt.uom_id
            WHERE pu.category_id != mu.category_id
              AND pt.uom_id IS NOT NULL
        )
        UPDATE stock_move sm
        SET product_uom = mismatches.new_uom
        FROM mismatches
        WHERE sm.id = mismatches.move_id
        """,
    )
    fixed_moves = env.cr.rowcount

    openupgrade.logged_query(
        env.cr,
        """
        WITH mismatches AS (
            SELECT sml.id AS line_id, pt.uom_id AS new_uom
            FROM stock_move_line sml
            JOIN product_product pp ON pp.id = sml.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom mu ON mu.id = sml.product_uom_id
            JOIN uom_uom pu ON pu.id = pt.uom_id
            WHERE pu.category_id != mu.category_id
              AND pt.uom_id IS NOT NULL
        )
        UPDATE stock_move_line sml
        SET product_uom_id = mismatches.new_uom
        FROM mismatches
        WHERE sml.id = mismatches.line_id
        """,
    )
    fixed_lines = env.cr.rowcount

    env.cr.execute(
        """
        SELECT COUNT(*)
        FROM stock_move sm
        JOIN uom_uom mu ON mu.id = sm.product_uom
        JOIN product_product pp ON sm.product_id = pp.id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN uom_uom pu ON pu.id = pt.uom_id
        WHERE pu.category_id != mu.category_id
        """
    )
    remaining_moves = env.cr.fetchone()[0]

    env.cr.execute(
        """
        SELECT COUNT(*)
        FROM stock_move_line sml
        JOIN product_product pp ON pp.id = sml.product_id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN uom_uom mu ON mu.id = sml.product_uom_id
        JOIN uom_uom pu ON pu.id = pt.uom_id
        WHERE pu.category_id != mu.category_id
          AND pt.uom_id IS NOT NULL
        """
    )
    remaining_lines = env.cr.fetchone()[0]

    _logger.info(
        "UoM fix: moves=%s lines=%s remaining_moves=%s remaining_lines=%s",
        fixed_moves,
        fixed_lines,
        remaining_moves,
        remaining_lines,
    )
    if remaining_moves or remaining_lines:
        _logger.warning(
            "UoM category mismatches remain: moves=%s lines=%s",
            remaining_moves,
            remaining_lines,
        )


@openupgrade.migrate()
def migrate(env, version):
    _fix_uom_category_mismatches(env)
    _fix_warehouse_related_companies(env)
    convert_company_dependent(env)
    _create_default_new_types_for_all_warehouses(env)
    _set_inter_company_locations(env)
    openupgrade.load_data(env, "stock", "18.0.1.1/noupdate_changes.xml")
    openupgrade.delete_records_safely_by_xml_id(
        env, ["stock.property_stock_customer", "stock.property_stock_supplier"]
    )
