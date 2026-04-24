# Copyright 2025 ForgeFlow S.L. (https://www.forgeflow.com)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "sales_team", "18.0.1.1/noupdate_changes.xml")
    openupgrade.delete_records_safely_by_xml_id(env, ["sales_team.ebay_sales_team"])
