import frappe
from frappe.utils import add_days, now_datetime

ENTRY = "Integration Log Entry"
SETTINGS = "Integration Log Settings"
ORPHAN_BATCH = 1000


def cleanup_done() -> int:
	"""Delete finished entries past the retention period and mirrors of vanished source rows.

	Open entries are only removed when their source row no longer exists (Frappe's
	log clearing deletes Error Logs without firing on_trash) or their source DocType is
	gone (consumer app uninstalled)."""
	return _delete_expired() + _delete_orphans()


def _delete_expired() -> int:
	retention = frappe.db.get_single_value(SETTINGS, "retention_days")
	retention = 90 if retention is None else int(retention)
	if retention <= 0:
		return 0
	filters = {
		"state": ("in", ("Acknowledged", "Resolved")),
		"modified": ("<", add_days(now_datetime(), -retention)),
	}
	count = frappe.db.count(ENTRY, filters)
	if count:
		frappe.db.delete(ENTRY, filters)
	return count


def _delete_orphans() -> int:
	deleted = 0
	doctypes = frappe.get_all(ENTRY, distinct=True, pluck="source_doctype")
	for doctype in doctypes:
		if not doctype:
			continue
		if not frappe.db.exists("DocType", doctype):
			count = frappe.db.count(ENTRY, {"source_doctype": doctype})
			frappe.db.delete(ENTRY, {"source_doctype": doctype})
			deleted += count
			continue
		if not frappe.db.table_exists(doctype):
			continue
		last = ""
		while True:
			rows = frappe.get_all(
				ENTRY,
				filters={"source_doctype": doctype, "name": (">", last)},
				fields=["name", "source_name"],
				order_by="name asc",
				limit=ORPHAN_BATCH,
			)
			if not rows:
				break
			last = rows[-1].name
			existing = set(
				frappe.get_all(doctype, filters={"name": ("in", [r.source_name for r in rows])}, pluck="name")
			)
			orphans = [r.name for r in rows if r.source_name not in existing]
			if orphans:
				frappe.db.delete(ENTRY, {"name": ("in", orphans)})
				deleted += len(orphans)
	return deleted
