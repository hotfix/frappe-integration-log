import frappe


def close_todo(source_name):
	"""retry_method used in tests: fixes the source row without going through save()."""
	frappe.db.set_value("ToDo", source_name, "status", "Closed")


def fail_retry(source_name):
	frappe.throw("nope", frappe.ValidationError)


def can_retry_only_for_marked(source_name):
	return frappe.db.get_value("ToDo", source_name, "description") == "retryable"
