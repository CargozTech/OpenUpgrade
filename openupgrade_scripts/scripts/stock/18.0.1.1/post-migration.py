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
            src_loc_id = values.get("default_location_src_id")
            dest_loc_id = values.get("default_location_dest_id")
            if src_loc_id:
                src_loc = env["stock.location"].browse(src_loc_id)
                if src_loc and src_loc.company_id and src_loc.company_id != wh.company_id:
                    src_loc.sudo()._write({"company_id": wh.company_id.id})
                    src_loc.invalidate_recordset(["company_id"])
            if dest_loc_id:
                dest_loc = env["stock.location"].browse(dest_loc_id)
                if (
                    dest_loc
                    and dest_loc.company_id
                    and dest_loc.company_id != wh.company_id
                ):
                    dest_loc.sudo()._write({"company_id": wh.company_id.id})
                    dest_loc.invalidate_recordset(["company_id"])
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
    """
    Migration fix for `stock_move.product_uom` category mismatches, applied
    only when a name-based UoM match exists in the product UoM category.

    Behavior:
    - Detect stock moves where move UoM category differs from product template UoM category.
    - Try to find a target UoM in the product category whose normalized name matches
      the move UoM normalized name (lower + trim + singularized trailing 's').
    - Update only matched rows; unmatched rows are left unchanged.
    """

    # Update only name-matched mismatches
    env.cr.execute(
        """
        WITH candidates AS (
            SELECT
                sm.id AS move_id,
                MIN(ut.id) AS new_uom_id
            FROM stock_move sm
            JOIN product_product pp ON pp.id = sm.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            JOIN uom_uom mu ON mu.id = sm.product_uom
            JOIN uom_uom pu ON pu.id = pt.uom_id
            JOIN uom_uom ut ON ut.category_id = pu.category_id
            WHERE pt.uom_id IS NOT NULL
              AND pu.category_id IS DISTINCT FROM mu.category_id
              AND regexp_replace(
                    lower(btrim(COALESCE(mu.name->>'en_US', mu.name::text))),
                    's$', '', 'g'
                  ) = regexp_replace(
                    lower(btrim(COALESCE(ut.name->>'en_US', ut.name::text))),
                    's$', '', 'g'
                  )
            GROUP BY sm.id
        )
        UPDATE stock_move sm
        SET product_uom = c.new_uom_id
        FROM candidates c
        WHERE sm.id = c.move_id
          AND sm.product_uom IS DISTINCT FROM c.new_uom_id;
        """
    )
    fixed_count = env.cr.rowcount

    # Remaining category mismatches (including non-name-matched rows)
    env.cr.execute(
        """
        SELECT COUNT(*)
        FROM stock_move sm
        JOIN uom_uom mu ON mu.id = sm.product_uom
        JOIN product_product pp ON sm.product_id = pp.id
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN uom_uom pu ON pu.id = pt.uom_id
        WHERE pt.uom_id IS NOT NULL
          AND pu.category_id IS DISTINCT FROM mu.category_id;
        """
    )
    remaining = env.cr.fetchone()[0]

    if fixed_count:
        _logger.info(
            "Fixed %s stock.move records by name-matched UoM remap; %s mismatches remain.",
            fixed_count,
            remaining,
        )
    else:
        _logger.info(
            "No name-matched stock.move UoM category mismatches found; %s mismatches remain.",
            remaining,
        )


def _fix_uom_category_mismatches_stock_move_line(env):
    """
    Fix UoM category mismatches in stock.move.line records.

    (A) Line UoM vs product — align to product_template.uom_id when categories differ.
    (B) Line UoM vs parent stock_move.product_uom — REQUIRED for stock.move
        _compute_quantity(): it converts each line qty from line.product_uom_id to
        move.product_uom; those two must share the same uom.category_id.
        Aligning only to the product is not enough if move.product_uom still differs.
    """
    # (A) Lines vs product template UoM
    env.cr.execute(
        """
           WITH line_vals AS (
                SELECT
                    sml.move_id,
                    MIN(sml.product_id) AS line_product_id,
                    MIN(sml.product_uom_id) AS line_uom_id
                FROM stock_move_line sml
                WHERE sml.product_id IS NOT NULL
                  AND sml.product_uom_id IS NOT NULL
                GROUP BY sml.move_id
                HAVING COUNT(DISTINCT sml.product_id) = 1
                   AND COUNT(DISTINCT sml.product_uom_id) = 1
            )
            UPDATE stock_move sm
            SET product_id = lv.line_product_id,
                product_uom = lv.line_uom_id
            FROM line_vals lv
            WHERE sm.id = lv.move_id
              AND  sm.product_id IS DISTINCT FROM lv.line_product_id
              ;
        """
    )
    fixed_sync = env.cr.rowcount
    if fixed_sync:
        _logger.info(
            "UoM fix: synced %s stock_move_line product+uom from parent stock_move",
            fixed_sync,
        )

    # # (A) Lines vs product template UoM (no JOIN on sml in FROM — PostgreSQL-safe)
    # env.cr.execute(
    #     """
    #     UPDATE stock_move_line
    #     SET product_uom_id = pt.uom_id
    #     FROM product_product pp
    #     JOIN product_template pt ON pt.id = pp.product_tmpl_id
    #     JOIN uom_uom uom_prod ON uom_prod.id = pt.uom_id
    #     WHERE stock_move_line.product_id = pp.id
    #       AND stock_move_line.product_uom_id IS NOT NULL
    #       AND EXISTS (
    #           SELECT 1 FROM uom_uom uom_line
    #           WHERE uom_line.id = stock_move_line.product_uom_id
    #             AND uom_line.category_id IS DISTINCT FROM uom_prod.category_id
    #       )
    #     """
    # )
    # fixed_sml_product = env.cr.rowcount
    # if fixed_sml_product:
    #     _logger.info(
    #         "UoM fix: aligned %s stock_move_line.product_uom_id to product uom",
    #         fixed_sml_product,
    #     )
    #
    # # (B1) stock.move._compute_quantity: line uom vs move.product_uom (same product)
    # env.cr.execute(
    #     """
    #     UPDATE stock_move_line sml
    #     SET product_uom_id = sm.product_uom
    #     FROM stock_move sm,
    #          uom_uom uom_move,
    #          uom_uom uom_line
    #     WHERE sml.move_id = sm.id
    #       AND sml.product_id = sm.product_id
    #       AND sm.product_uom IS NOT NULL
    #       AND sml.product_uom_id IS NOT NULL
    #       AND uom_move.id = sm.product_uom
    #       AND uom_line.id = sml.product_uom_id
    #       AND uom_line.category_id IS DISTINCT FROM uom_move.category_id
    #     """
    # )
    # fixed_sml_move = env.cr.rowcount
    # if fixed_sml_move:
    #     _logger.info(
    #         "UoM fix: aligned %s stock_move_line (line vs move → move uom)",
    #         fixed_sml_move,
    #     )
    #
    # # Second pass: line vs product (after line vs move alignment)
    # env.cr.execute(
    #     """
    #     UPDATE stock_move_line
    #     SET product_uom_id = pt.uom_id
    #     FROM product_product pp
    #     JOIN product_template pt ON pt.id = pp.product_tmpl_id
    #     JOIN uom_uom uom_prod ON uom_prod.id = pt.uom_id
    #     WHERE stock_move_line.product_id = pp.id
    #       AND stock_move_line.product_uom_id IS NOT NULL
    #       AND EXISTS (
    #           SELECT 1 FROM uom_uom uom_line
    #           WHERE uom_line.id = stock_move_line.product_uom_id
    #             AND uom_line.category_id IS DISTINCT FROM uom_prod.category_id
    #       )
    #     """
    # )
    # fixed_sml_pass2 = env.cr.rowcount
    # if fixed_sml_pass2:
    #     _logger.info(
    #         "UoM fix: second pass aligned %s stock_move_line vs product uom",
    #         fixed_sml_pass2,
    #     )


def _fix_uom_category_mismatches_account_move_line(env):
    """Align account_move_line.product_uom_id to product template uom when categories differ."""
    env.cr.execute(
        """
            WITH candidates AS (
                SELECT
                    aml.id AS aml_id,
                    MIN(ut.id) AS new_uom_id
                FROM account_move_line aml
                JOIN product_product pp ON aml.product_id = pp.id
                JOIN product_template pt ON pt.id = pp.product_tmpl_id
                JOIN uom_uom uom_prod ON uom_prod.id = pt.uom_id
                JOIN uom_uom uom_line ON uom_line.id = aml.product_uom_id
                JOIN uom_uom ut ON ut.category_id = uom_prod.category_id
                WHERE aml.product_id IS NOT NULL
                  AND aml.product_uom_id IS NOT NULL
                  AND uom_line.category_id IS DISTINCT FROM uom_prod.category_id
                  AND regexp_replace(
                        lower(btrim(
                            coalesce(
                                uom_line.name->>'en_US',
                                uom_line.name->>'en_IN',
                                uom_line.name::text
                            )
                        )),
                        's$', '', 'g'
                      ) =
                      regexp_replace(
                        lower(btrim(
                            coalesce(
                                ut.name->>'en_US',
                                ut.name->>'en_IN',
                                ut.name::text
                            )
                        )),
                        's$', '', 'g'
                      )
                GROUP BY aml.id
            )
            UPDATE account_move_line aml
            SET product_uom_id = c.new_uom_id
            FROM candidates c
            WHERE aml.id = c.aml_id
              AND aml.product_uom_id IS DISTINCT FROM c.new_uom_id;
        """
    )
    n = env.cr.rowcount
    if n:
        _logger.info(
            "UoM fix: aligned %s account_move_line records to product uom",
            n,
        )


@openupgrade.migrate()
def migrate(env, version):
    convert_company_dependent(env)
    # Fix company consistency before creating operation types.
    _fix_warehouse_related_companies(env)
    _set_inter_company_locations(env)
    _create_default_new_types_for_all_warehouses(env)
    # UoM category fixes (single source of truth; do not duplicate in odi migrations).
    _fix_uom_category_mismatches(env)
    _fix_uom_category_mismatches_stock_move_line(env)
    _fix_uom_category_mismatches_account_move_line(env)
    openupgrade.load_data(env, "stock", "18.0.1.1/noupdate_changes.xml")
    openupgrade.delete_records_safely_by_xml_id(
        env, ["stock.property_stock_customer", "stock.property_stock_supplier"]
    )
