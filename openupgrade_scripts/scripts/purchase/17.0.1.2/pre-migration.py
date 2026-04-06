from openupgradelib import openupgrade

def fix_purchase_line_uom_category_mismatches(env):
    """
    Fix purchase lines only when UoM categories mismatch AND a name-based
    match exists inside the product UoM category.
    Example matches: Unit/Units, Day/Days (via normalization).
    """
    openupgrade.logged_query(
        env.cr,
        """
        WITH candidates AS (
            SELECT
                pol.id AS pol_id,
                MIN(u_target.id) AS new_uom_id
            FROM purchase_order_line pol
            JOIN product_product pp ON pol.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            JOIN uom_uom u_line ON u_line.id = pol.product_uom
            JOIN uom_uom u_prod ON u_prod.id = pt.uom_id
            JOIN uom_uom u_target
                ON u_target.category_id = u_prod.category_id
            WHERE pol.product_id IS NOT NULL
              AND u_line.category_id IS DISTINCT FROM u_prod.category_id
              AND regexp_replace(
                    lower(btrim(COALESCE(u_line.name->>'en_US', u_line.name::text))),
                    's$', '', 'g'
                  ) = regexp_replace(
                    lower(btrim(COALESCE(u_target.name->>'en_US', u_target.name::text))),
                    's$', '', 'g'
                  )
            GROUP BY pol.id
        )
        UPDATE purchase_order_line pol
        SET product_uom = c.new_uom_id
        FROM candidates c
        WHERE pol.id = c.pol_id
          AND pol.product_uom IS DISTINCT FROM c.new_uom_id
        """,
    )


@openupgrade.migrate()
def migrate(env, version=None):
    # Fix UoM category mismatches before any other operations
    fix_purchase_line_uom_category_mismatches(env)
    if not openupgrade.column_exists(env.cr, "purchase_order_line", "discount"):
        openupgrade.logged_query(
            env.cr,
            "ALTER TABLE purchase_order_line ADD COLUMN discount FLOAT DEFAULT 0",
        )
        openupgrade.logged_query(
            env.cr,
            "ALTER TABLE purchase_order_line ALTER COLUMN discount DROP DEFAULT",
        )
