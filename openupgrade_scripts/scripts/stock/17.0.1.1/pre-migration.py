# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openupgradelib import openupgrade

_field_renames = [
    ("stock.move", "stock_move", "quantity_done", "quantity"),
]

_column_copies = {
    "stock_move_line": [
        ("qty_done", "quantity", None),
    ]
}


def fix_move_line_quantity(env):
    """
    v17 combines what used to be reserved_qty and qty_done.
    We assume that we shouldn't touch an original qty_done on
    done moves, but that we can best reflect the v16 state of
    lines being worked on by adding reserved_qty to the new
    quantity column, which was qty_done in v16

    In post-migration, we'll recompute the quantity field of
    moves affected.
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE stock_move_line
        SET quantity = quantity + reserved_qty
        WHERE
        state IN ('assigned', 'partially_available')
        AND reserved_qty <> 0
        """,
    )

def fix_uom_category_mismatches(env):
    """
    Fix stock move lines where the product_uom_id is in a different category
    than the product's uom_id. This causes errors during quantity_product_uom
    computation in Odoo 17.
    We fix this by updating the stock move line's product_uom_id to match
    the product's uom_id when there's a category mismatch.
    """
    openupgrade.logged_query(
        env.cr,
        """
            WITH candidates AS (
                SELECT
                    sml.id AS sml_id,
                    MIN(ut.id) AS new_uom_id
                FROM stock_move_line sml
                JOIN product_product pp ON sml.product_id = pp.id
                JOIN product_template pt ON pp.product_tmpl_id = pt.id
                JOIN uom_uom u1 ON sml.product_uom_id = u1.id
                JOIN uom_uom u2 ON pt.uom_id = u2.id
                JOIN uom_uom ut ON ut.category_id = u2.category_id
                WHERE sml.product_id IS NOT NULL
                  AND sml.product_uom_id IS NOT NULL
                  AND u1.category_id IS DISTINCT FROM u2.category_id
                  AND regexp_replace(
                        lower(btrim(COALESCE(u1.name->>'en_US', u1.name::text))),
                        's$', '', 'g'
                      ) =
                      regexp_replace(
                        lower(btrim(COALESCE(ut.name->>'en_US', ut.name::text))),
                        's$', '', 'g'
                      )
                GROUP BY sml.id
            )
            UPDATE stock_move_line sml
            SET product_uom_id = c.new_uom_id
            FROM candidates c
            WHERE sml.id = c.sml_id
            AND sml.product_uom_id IS DISTINCT FROM c.new_uom_id;
            """,
    )


@openupgrade.migrate()
def migrate(env, version):
    # Fix UoM category mismatches before any other operations
    fix_uom_category_mismatches(env)
    openupgrade.rename_fields(env, _field_renames)
    openupgrade.copy_columns(env.cr, _column_copies)
    fix_move_line_quantity(env)
