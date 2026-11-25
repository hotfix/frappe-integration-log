import frappe


def close_todo(source_name):
	"""retry_method used in tests: fixes the source row without going through save()."""
	frappe.db.set_value("ToDo", source_name, "status", "Closed")


def fail_retry(source_name):
	frappe.throw("nope", frappe.ValidationError)


def can_retry_only_for_marked(source_name):
	return frappe.db.get_value("ToDo", source_name, "description") == "retryable"


def write_then_fail(source_name):
	frappe.db.set_value("ToDo", source_name, "description", "partial write")
	frappe.throw("late failure", frappe.ValidationError)


TEST_SOURCE = "Test Source"


def cleanup_test_data(testcase):
	"""Register a cleanup that removes every ToDo created during the test and all
	mirrors with the test source, even when the test committed or failed midway.

	Pre-existing ToDos (identified by name before the test) are never touched.
	"""
	existing = set(frappe.get_all("ToDo", pluck="name"))

	def _cleanup():
		frappe.db.rollback()
		created = [n for n in frappe.get_all("ToDo", pluck="name") if n not in existing]
		frappe.db.delete("Integration Log Entry", {"source": TEST_SOURCE})
		if created:
			frappe.db.delete(
				"Integration Log Entry", {"source_doctype": "ToDo", "source_name": ("in", created)}
			)
			frappe.db.delete("ToDo", {"name": ("in", created)})
		frappe.db.commit()

	testcase.addCleanup(_cleanup)
