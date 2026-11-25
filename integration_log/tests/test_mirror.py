from unittest.mock import patch

import frappe
import pymysql
from frappe.tests import IntegrationTestCase

from integration_log import api, mirror, registry

ENTRY = "Integration Log Entry"
SETTINGS = "Integration Log Settings"
FAILED_TITLE = "Integration Log mirror failed: ToDo"


def todo_entry(**overrides):
	entry = {
		"doctype": "ToDo",
		"source": "Test Source",
		"status_field": "status",
		"severity_map": {"Open": "error", "Cancelled": "warning", "Closed": "info"},
		"message_field": "description",
		"group_fields": ["reference_type", "reference_name"],
		"link_fields": ["reference_type", "reference_name"],
	}
	entry.update(overrides)
	return entry


def raising_severity(doc):
	raise RuntimeError("severity boom")


class TestMirror(IntegrationTestCase):
	def setUp(self):
		self._register([todo_entry()])

	def tearDown(self):
		frappe.db.rollback()
		frappe.db.set_single_value(SETTINGS, "include_error_log", 0)
		frappe.clear_document_cache(SETTINGS)
		registry.clear_cache()

	def _register(self, entries):
		registry.clear_cache()
		with patch.object(registry, "_load_hook_entries", return_value=entries):
			registry.get_registry()

	def _todo(self, description="Sync failed\nsecond line", status="Open", ref="Administrator"):
		return frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": description,
				"status": status,
				"reference_type": "User" if ref else None,
				"reference_name": ref,
			}
		).insert(ignore_permissions=True)

	def _mirror(self, todo):
		name = frappe.db.get_value(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name})
		return frappe.get_doc(ENTRY, name) if name else None

	def _set_status(self, todo, status):
		todo.reload()
		todo.status = status
		todo.save(ignore_permissions=True)

	def test_error_creates_open_mirror(self):
		todo = self._todo()
		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Open")
		self.assertEqual(entry.severity, "error")
		self.assertEqual(entry.title, "Sync failed")
		self.assertEqual(entry.source, "Test Source")
		self.assertEqual(entry.source_status, "Open")
		self.assertEqual(entry.ref_doctype, "User")
		self.assertEqual(entry.ref_name, "Administrator")
		self.assertEqual(entry.group_key, mirror.group_key_of(registry.get_source("ToDo"), todo))
		self.assertEqual(str(entry.occurred_on), str(todo.creation))
		self.assertEqual(frappe.db.count(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name}), 1)

	def test_warning_mirrored(self):
		entry = self._mirror(self._todo(status="Cancelled"))
		self.assertEqual((entry.state, entry.severity), ("Open", "warning"))

	def test_info_not_mirrored(self):
		self.assertIsNone(self._mirror(self._todo(status="Closed")))

	def test_same_row_error_then_info_resolves(self):
		todo = self._todo()
		self._set_status(todo, "Closed")
		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Resolved")
		self.assertEqual(entry.resolved_automatically, 1)

	def test_resolved_row_error_again_reopens(self):
		todo = self._todo()
		self._set_status(todo, "Closed")
		self._set_status(todo, "Open")
		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Open")
		self.assertEqual(entry.resolved_automatically, 0)

	def test_acknowledged_stays_acknowledged(self):
		todo = self._todo()
		entry = self._mirror(todo)
		entry.state = "Acknowledged"
		entry.save(ignore_permissions=True)

		self._set_status(todo, "Cancelled")
		self._set_status(todo, "Open")
		self.assertEqual(self._mirror(todo).state, "Acknowledged")

		self._set_status(todo, "Closed")
		self.assertEqual(self._mirror(todo).state, "Acknowledged")

		other = self._todo()
		self.assertEqual(self._mirror(other).state, "Open")

	def test_group_resolution(self):
		first = self._todo()
		second = self._todo()
		other_ref = self._todo(ref="Guest")
		closed = self._todo(status="Closed")
		self.assertIsNone(self._mirror(closed))
		self.assertEqual(self._mirror(first).state, "Resolved")
		self.assertEqual(self._mirror(first).resolved_automatically, 1)
		self.assertEqual(self._mirror(second).state, "Resolved")
		self.assertEqual(self._mirror(other_ref).state, "Open")

		newer = self._todo()
		closed.save(ignore_permissions=True)  # re-run handler for the older success row
		self.assertEqual(self._mirror(newer).state, "Open")

	def test_group_resolution_skips_rows_without_group_values(self):
		lone = self._todo(ref=None)
		self._todo(status="Closed", ref=None)
		self.assertEqual(self._mirror(lone).state, "Open")

	def _group_resolved(self):
		todo = self._todo()
		self._todo(status="Closed")
		self.assertEqual(self._mirror(todo).state, "Resolved")
		return todo

	def test_group_resolved_stays_resolved_on_sync(self):
		todo = self._group_resolved()
		mirror.sync("ToDo", todo.name)
		mirror.on_doc_event(frappe.get_doc("ToDo", todo.name))
		self.assertEqual(self._mirror(todo).state, "Resolved")

	def test_group_resolved_stays_resolved_on_backfill(self):
		todo = self._group_resolved()
		with patch.object(frappe.db, "commit"):
			api.backfill("ToDo")
			api.backfill("ToDo")
		self.assertEqual(self._mirror(todo).state, "Resolved")

	def test_group_resolved_reopens_when_row_updated(self):
		todo = self._group_resolved()
		todo.reload()
		todo.description = "Sync failed again"
		todo.save(ignore_permissions=True)
		entry = self._mirror(todo)
		self.assertEqual((entry.state, entry.resolved_automatically), ("Open", 0))
		self.assertEqual(entry.title, "Sync failed again")

	def test_db_set_is_mirrored(self):
		todo = self._todo()
		todo.db_set("status", "Closed")
		entry = self._mirror(todo)
		self.assertEqual((entry.state, entry.source_status), ("Resolved", "Closed"))

	def test_registry_failure_never_raises(self):
		todo = self._todo()
		with (
			patch.object(registry, "get_source", side_effect=ConnectionError("redis down")),
			patch.object(mirror, "_logger") as logger,
		):
			self._set_status(todo, "Closed")
			self.assertIsNone(mirror.sync("ToDo", todo.name))
			mirror.on_doc_trash(todo)
		# one per call above, plus calls for other documents the save writes (e.g. Version)
		self.assertGreaterEqual(logger.return_value.exception.call_count, 3)

	def test_failed_savepoint_rollback_propagates(self):
		real_rollback = frappe.db.rollback

		def rollback(*args, **kwargs):
			if kwargs.get("save_point", "").startswith("integration_log_mirror_"):
				raise RuntimeError("savepoint does not exist")
			return real_rollback(*args, **kwargs)

		with (
			patch.object(mirror, "_apply", side_effect=ValueError("mirror boom")),
			patch.object(frappe.db, "rollback", side_effect=rollback),
			patch.object(mirror, "_report_failure") as report,
			patch.object(mirror, "_logger") as logger,
			self.assertRaises(ValueError) as ctx,
		):
			self._todo()
		self.assertEqual(str(ctx.exception), "mirror boom")
		report.assert_not_called()
		self.assertTrue(logger.return_value.exception.called)

	def test_deadlock_propagates(self):
		deadlocks = (
			frappe.QueryDeadlockError("Deadlock found"),
			pymysql.err.OperationalError(1213, "Deadlock found when trying to get lock"),
		)
		for exc in deadlocks:
			with (
				patch.object(mirror, "_apply", side_effect=exc),
				patch.object(mirror, "_report_failure") as report,
				self.assertRaises(type(exc)),
			):
				self._todo()
			report.assert_not_called()

	def test_trash_deletes_mirror(self):
		todo = self._todo()
		self.assertIsNotNone(self._mirror(todo))
		todo.delete(ignore_permissions=True)
		self.assertFalse(frappe.db.exists(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name}))

	def test_rollback_removes_mirror(self):
		frappe.db.savepoint("il_test_outer")
		todo = self._todo()
		self.assertIsNotNone(self._mirror(todo))
		frappe.db.rollback(save_point="il_test_outer")
		self.assertFalse(frappe.db.exists(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name}))

	def test_mirror_failure_does_not_break_source(self):
		self._register([todo_entry(severity_method="integration_log.tests.test_mirror.raising_severity")])
		frappe.db.delete("Error Log", {"method": FAILED_TITLE})
		todo = self._todo()
		self.assertTrue(frappe.db.exists("ToDo", todo.name))
		self.assertIsNone(self._mirror(todo))
		self.assertEqual(frappe.db.count("Error Log", {"method": FAILED_TITLE}), 1)

		self._todo()
		self.assertEqual(frappe.db.count("Error Log", {"method": FAILED_TITLE}), 1)

	def test_failure_after_partial_write_rolls_back_mirror_only(self):
		todo = self._todo()
		with patch.object(mirror, "_resolve_group", side_effect=RuntimeError("late boom")):
			self._set_status(todo, "Closed")
		# own-row resolution was rolled back together with the failing group step
		self.assertEqual(self._mirror(todo).state, "Open")
		self.assertEqual(frappe.db.get_value("ToDo", todo.name, "status"), "Closed")

	def test_unregistered_doctype_costs_no_query(self):
		note = frappe.get_doc({"doctype": "Note", "title": "il-note", "content": "x"})
		note.insert(ignore_permissions=True)
		with patch.object(frappe.db, "sql") as sql:
			mirror.on_doc_event(note, "on_update")
			mirror.on_doc_trash(note, "on_trash")
		sql.assert_not_called()

	def test_long_message_truncated(self):
		todo = self._todo(description="x" * 20000)
		entry = self._mirror(todo)
		self.assertEqual(len(entry.message), 10000)
		self.assertEqual(len(entry.title), 140)

	def test_context_json(self):
		self._register([todo_entry(context_fields=["status", "reference_name"])])
		entry = self._mirror(self._todo())
		expected = {"status": "Open", "reference_name": "Administrator"}
		self.assertEqual(frappe.parse_json(entry.context), expected)

	def test_title_field(self):
		self._register([todo_entry(title_field="reference_name")])
		self.assertEqual(self._mirror(self._todo()).title, "Administrator")

	def test_sync_after_set_value(self):
		todo = self._todo()
		frappe.db.set_value("ToDo", todo.name, "status", "Closed")
		self.assertEqual(self._mirror(todo).state, "Open")
		name = mirror.sync("ToDo", todo.name)
		self.assertEqual(name, self._mirror(todo).name)
		self.assertEqual(self._mirror(todo).state, "Resolved")

	def test_sync_unregistered_or_missing(self):
		self.assertIsNone(mirror.sync("Note", "whatever"))
		self.assertIsNone(mirror.sync("ToDo", "does-not-exist"))

	def test_group_key_of(self):
		config = registry.get_source("ToDo")
		a = frappe._dict(reference_type="User", reference_name="Administrator")
		b = frappe._dict(reference_type="User", reference_name="Guest")
		self.assertEqual(len(mirror.group_key_of(config, a)), 40)
		self.assertNotEqual(mirror.group_key_of(config, a), mirror.group_key_of(config, b))
		self.assertIsNone(mirror.group_key_of(registry.ERROR_LOG_SOURCE, a))

	def test_severity_of(self):
		config = registry.get_source("ToDo")
		self.assertEqual(mirror.severity_of(config, frappe._dict(status="Cancelled")), "warning")
		self.assertEqual(mirror.severity_of(registry.ERROR_LOG_SOURCE, frappe._dict()), "error")

	def _enable_error_log(self, entries=()):
		frappe.db.set_single_value(SETTINGS, "include_error_log", 1)
		frappe.clear_document_cache(SETTINGS)
		self._register(list(entries))
		self.assertIn("Error Log", registry.registered_doctypes())

	def test_error_log_source(self):
		self._enable_error_log()
		log = frappe.log_error("x")
		entry = frappe.get_doc(ENTRY, {"source_doctype": "Error Log", "source_name": log.name})
		self.assertEqual(entry.severity, "error")
		self.assertEqual(entry.source, "System")
		self.assertEqual(entry.title, "x")

	def test_error_log_source_failure_does_not_recurse(self):
		self._enable_error_log()
		before = frappe.db.count("Error Log")
		with (
			patch.object(mirror, "_apply", side_effect=RuntimeError("boom")),
			patch.object(mirror, "_logger") as logger,
		):
			log = frappe.log_error("y")
		self.assertEqual(frappe.db.count("Error Log") - before, 1)
		self.assertFalse(frappe.db.exists(ENTRY, {"source_doctype": "Error Log", "source_name": log.name}))
		self.assertTrue(logger.return_value.exception.called)
