from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from integration_log import registry

ERROR_TITLE = registry.INVALID_ENTRY_TITLE


def todo_entry(**overrides):
	entry = {
		"doctype": "ToDo",
		"source": "Todos",
		"status_field": "status",
		"severity_map": {"Open": "error", "Closed": "info"},
		"message_field": "description",
		"group_fields": ["reference_type", "reference_name"],
	}
	entry.update(overrides)
	return entry


class TestRegistry(IntegrationTestCase):
	def tearDown(self):
		registry.clear_cache()

	def _build(self, entries):
		registry.clear_cache()
		with patch.object(registry, "_load_hook_entries", return_value=entries):
			return registry.get_registry()

	def _error_count(self):
		return frappe.db.count("Error Log", {"method": ERROR_TITLE})

	def test_severity_rank(self):
		self.assertEqual([registry.severity_rank(s) for s in registry.SEVERITIES], [0, 1, 2])

	def test_valid_entry_parsed(self):
		reg = self._build(
			[todo_entry(context_fields=["owner"], link_fields=["reference_type", "reference_name"])]
		)
		source = reg["ToDo"]
		self.assertEqual(source.severity_map["Open"], "error")
		self.assertEqual(source.mirror_levels, frozenset({"error", "warning"}))
		self.assertEqual(source.group_fields, ("reference_type", "reference_name"))
		self.assertEqual(source.context_fields, ("owner",))
		self.assertEqual(source.link_fields, ("reference_type", "reference_name"))
		with patch.object(registry, "_load_hook_entries", return_value=[todo_entry()]):
			self.assertEqual(registry.get_source("ToDo"), source)
			self.assertIsNone(registry.get_source("User"))

	def test_invalid_entries_are_ignored(self):
		before = self._error_count()
		entries = [
			todo_entry(doctype=None, source="NoDoctype"),
			todo_entry(doctype="No Such Doctype"),
			todo_entry(doctype="User", severity_map={"Open": "fatal"}),
			todo_entry(doctype="Note", message_field=None),
			todo_entry(),
		]
		reg = self._build(entries)
		self.assertEqual(set(reg), {"ToDo"})
		self.assertEqual(self._error_count() - before, 4)

	def test_malformed_entries_do_not_break_registry(self):
		before = self._error_count()
		entries = [
			todo_entry(doctype="User", link_fields=5),
			todo_entry(doctype="Note", mirror_levels=5),
			todo_entry(doctype="Role", severity_map=["x"]),
			todo_entry(doctype="Country", context_fields=5),
			todo_entry(),
		]
		reg = self._build(entries)
		self.assertEqual(set(reg), {"ToDo"})
		self.assertEqual(self._error_count() - before, 4)

	def test_empty_mirror_levels_kept(self):
		reg = self._build([todo_entry(mirror_levels=[])])
		self.assertEqual(reg["ToDo"].mirror_levels, frozenset())

	def test_invalid_severity_method_result_is_warning(self):
		config = self._build([todo_entry(severity_method="x.y")])["ToDo"]
		with patch("frappe.get_attr", return_value=lambda doc: "fatal"):
			self.assertEqual(registry.resolve_severity(config, frappe._dict()), "warning")

	def test_reentrant_log_error_does_not_recurse(self):
		calls = []
		real = frappe.log_error

		def reentrant(*args, **kwargs):
			calls.append(1)
			registry.registered_doctypes()
			return real(*args, **kwargs)

		registry.clear_cache()
		before = self._error_count()
		with (
			patch.object(registry, "_load_hook_entries", return_value=[todo_entry(), todo_entry()]),
			patch.object(frappe, "log_error", side_effect=reentrant),
		):
			self.assertEqual(registry.registered_doctypes(), frozenset({"ToDo"}))
		self.assertEqual(len(calls), 1)
		self.assertEqual(self._error_count() - before, 1)

	def test_duplicate_doctype_first_wins(self):
		before = self._error_count()
		reg = self._build([todo_entry(source="First"), todo_entry(source="Second")])
		self.assertEqual(reg["ToDo"].source, "First")
		self.assertEqual(self._error_count() - before, 1)

	def test_resolve_severity(self):
		config = self._build([todo_entry()])["ToDo"]
		self.assertEqual(registry.resolve_severity(config, frappe._dict(status="Open")), "error")
		self.assertEqual(registry.resolve_severity(config, frappe._dict(status="Weird")), "warning")
		self.assertEqual(registry.resolve_severity(registry.ERROR_LOG_SOURCE, frappe._dict()), "error")

	def test_registered_doctypes_cached_without_query(self):
		self._build([todo_entry()])
		with (
			patch.object(registry, "_load_hook_entries", return_value=[todo_entry()]),
			patch.object(frappe.db, "sql") as sql,
		):
			self.assertEqual(registry.registered_doctypes(), frozenset({"ToDo"}))
			self.assertEqual(registry.registered_doctypes(), frozenset({"ToDo"}))
			sql.assert_not_called()

	def test_cache_survives_local_reset(self):
		self._build([todo_entry()])
		frappe.local.integration_log_registry = None
		with patch.object(registry, "_load_hook_entries", side_effect=AssertionError("rebuilt")):
			self.assertIn("ToDo", registry.get_registry())

	def test_error_log_source_only_when_enabled(self):
		original = frappe.db.get_single_value("Integration Log Settings", "include_error_log")
		try:
			frappe.db.set_single_value("Integration Log Settings", "include_error_log", 0)
			self.assertNotIn("Error Log", self._build([]))
			frappe.db.set_single_value("Integration Log Settings", "include_error_log", 1)
			frappe.clear_document_cache("Integration Log Settings")
			source = self._build([])["Error Log"]
			self.assertEqual(source.source, "System")
			self.assertIsNone(source.status_field)
			self.assertEqual(source.message_field, "error")
			self.assertEqual(source.link_fields, ("reference_doctype", "reference_name"))
		finally:
			frappe.db.set_single_value("Integration Log Settings", "include_error_log", original or 0)
