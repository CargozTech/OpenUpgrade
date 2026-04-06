# Copyright 2025 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from openupgradelib import openupgrade

def fix_sale_line_uom_category_mismatches(env):
    """
    Fix sale order lines where the product_uom is in a different category
    than the product's uom_id. This causes errors during product_qty
    computation in Odoo 17 delivery module.
    """
    openupgrade.logged_query(
        env.cr,
        """
            WITH candidates AS (
                SELECT
                    sol.id AS sol_id,
                    MIN(u_target.id) AS new_uom_id
                FROM sale_order_line sol
                JOIN product_product pp ON sol.product_id = pp.id
                JOIN product_template pt ON pp.product_tmpl_id = pt.id
                JOIN uom_uom u1 ON u1.id = sol.product_uom
                JOIN uom_uom u_prod ON u_prod.id = pt.uom_id
                JOIN uom_uom u_target ON u_target.category_id = u_prod.category_id
                WHERE sol.product_id IS NOT NULL
                  AND u1.category_id IS DISTINCT FROM u_prod.category_id
                  AND regexp_replace(lower(btrim(COALESCE(u1.name->>'en_US', u1.name::text))), 's$', '', 'g')
                      = regexp_replace(lower(btrim(COALESCE(u_target.name->>'en_US', u_target.name::text))), 's$', '', 'g')
                GROUP BY sol.id
            )
            UPDATE sale_order_line sol
            SET product_uom = c.new_uom_id
            FROM candidates c
            WHERE sol.id = c.sol_id
              AND sol.product_uom IS DISTINCT FROM c.new_uom_id;
        """,
    )



@openupgrade.migrate()
def migrate(env, version):
    # Fix UoM category mismatches before any other operations
    fix_sale_line_uom_category_mismatches(env)
    openupgrade.copy_columns(env.cr, {"sale_order": [("state", None, None)]})
