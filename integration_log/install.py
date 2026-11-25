import frappe

from integration_log import registry

ROLE = "Integration Log Manager"


def after_install():
	ensure_role()
	ensure_unique_index()
	registry.clear_cache()


def ensure_role():
	if frappe.db.exists("Role", ROLE):
		return
	frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)


def ensure_unique_index():
	if not frappe.db.table_exists("Integration Log Entry"):
		return
	frappe.db.add_unique("Integration Log Entry", ["source_doctype", "source_name"])
