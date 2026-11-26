from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from integration_log import api, mirror, registry
from integration_log.tests.helpers import cleanup_test_data
from integration_log.tests.test_mirror import todo_entry

ENTRY = "Integration Log Entry"
SETTINGS = "Integration Log Settings"
ROW = "Integration Log Source Setting"


class TestSourceSettings(IntegrationTestCase):
	def setUp(self):
		cleanup_test_data(self)
		self._saved_rows = frappe.get_all(ROW, filters={"parent": SETTINGS}, fields=["*"], order_by="idx asc")
		# Hook entries stay patched for the whole test, so a rebuild after a settings save
		# (cache cleared by on_update) sees the test source and the new rows
		hooks = patch.object(registry, "_load_hook_entries", return_value=[todo_entry()])
		hooks.start()
		self.addCleanup(hooks.stop)
		registry.clear_cache()

	def tearDown(self):
		frappe.db.rollback()
		# backfill commits: restore the settings rows as they were before the test
		frappe.db.delete(ROW, {"parent": SETTINGS})
		for row in self._saved_rows:
			frappe.get_doc({"doctype": ROW, **row}).db_insert()
		frappe.db.commit()
		frappe.clear_document_cache(SETTINGS)
		registry.clear_cache()

	def _settings(self):
		frappe.clear_document_cache(SETTINGS)
		return frappe.get_doc(SETTINGS)

	def _set_rows(self, *rows):
		settings = self._settings()
		settings.set("sources", [])
		for row in rows:
			settings.append("sources", row)
		settings.save()

	def _todo(self, status="Open", description="Sync failed", ref=False):
		values = {"doctype": "ToDo", "description": description, "status": status}
		if ref:
			values.update(reference_type="User", reference_name="Administrator")
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def _mirror(self, todo):
		name = frappe.db.get_value(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name})
		return frappe.get_doc(ENTRY, name) if name else None

	def _save_todo(self, todo, **values):
		todo.reload()
		todo.update(values)
		todo.save(ignore_permissions=True)

	def test_effective_levels(self):
		self.assertEqual(registry.effective_levels("info"), frozenset({"info", "warning", "error"}))
		self.assertEqual(registry.effective_levels("warning"), frozenset({"warning", "error"}))
		self.assertEqual(registry.effective_levels("error"), frozenset({"error"}))

	def test_defaults_without_rows(self):
		self._set_rows()
		config = registry.get_source("ToDo")
		self.assertTrue(config.enabled)
		self.assertEqual(config.mirror_levels, registry.DEFAULT_MIRROR_LEVELS)
		self.assertEqual(self._mirror(self._todo()).state, "Open")
		self.assertIsNone(self._mirror(self._todo(status="Closed")))

	def test_disabled_source_not_mirrored(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 0, "min_severity": "error"})
		self.assertFalse(registry.get_source("ToDo").enabled)
		todo = self._todo()
		self.assertIsNone(self._mirror(todo))
		self.assertIsNone(mirror.sync("ToDo", todo.name))
		self.assertIsNone(self._mirror(todo))

	def test_disabled_source_keeps_existing_entry(self):
		todo = self._todo()
		before = self._mirror(todo)
		self.assertEqual(before.state, "Open")

		self._set_rows({"source_doctype": "ToDo", "enabled": 0})
		self._save_todo(todo, status="Closed", description="changed")
		mirror.sync("ToDo", todo.name)
		api.backfill("ToDo")
		api.backfill()

		after = self._mirror(todo)
		self.assertEqual(after.state, "Open")
		self.assertEqual(after.source_status, "Open")
		self.assertEqual(after.message, "Sync failed")
		self.assertEqual(str(after.modified), str(before.modified))

	def test_disabled_source_trash_deletes_mirror(self):
		todo = self._todo()
		self.assertIsNotNone(self._mirror(todo))
		self._set_rows({"source_doctype": "ToDo", "enabled": 0})
		frappe.delete_doc("ToDo", todo.name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name}))

	def test_min_severity_info_mirrors_info(self):
		self.assertIsNone(self._mirror(self._todo(status="Closed")))
		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "info"})
		entry = self._mirror(self._todo(status="Closed"))
		self.assertEqual(entry.severity, "info")
		# Info entries are history: created resolved, never Open
		self.assertEqual(entry.state, "Resolved")
		self.assertTrue(entry.resolved_automatically)

	def test_info_entry_resolves_earlier_errors_of_group(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "info"})
		error = self._todo(ref=True)
		self.assertEqual(self._mirror(error).state, "Open")
		success = self._todo(status="Closed", ref=True)
		self.assertEqual(self._mirror(success).state, "Resolved")
		self.assertEqual(self._mirror(error).state, "Resolved")
		self.assertTrue(self._mirror(error).resolved_automatically)

	def test_info_entry_reopens_when_row_turns_error(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "info"})
		todo = self._todo(status="Closed")
		self.assertEqual(self._mirror(todo).state, "Resolved")
		self._save_todo(todo, status="Open")
		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Open")
		self.assertEqual(entry.severity, "error")
		self.assertFalse(entry.resolved_automatically)

	def test_info_keeps_acknowledged(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "info"})
		todo = self._todo()
		api.acknowledge([self._mirror(todo).name])
		self._save_todo(todo, status="Closed")
		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Acknowledged")
		self.assertEqual(entry.severity, "info")

	def test_area_refreshed_when_row_drops_below_level(self):
		with patch.object(registry, "_load_hook_entries", return_value=[todo_entry(area_field="priority")]):
			registry.clear_cache()
			todo = self._todo()
			self.assertEqual(self._mirror(todo).area, "Medium")
			for state in ("Open", "Acknowledged"):
				self._save_todo(todo, status="Open", priority="Medium")
				entry = self._mirror(todo)
				if state == "Acknowledged":
					api.reopen([entry.name])
					api.acknowledge([entry.name])
				self._save_todo(todo, status="Closed", priority="High")
				entry = self._mirror(todo)
				self.assertEqual(entry.area, "High")
				self.assertEqual(entry.state, "Resolved" if state == "Open" else "Acknowledged")
			registry.clear_cache()

	def test_min_severity_error_resolves_warning_on_change(self):
		todo = self._todo(status="Cancelled")
		self.assertEqual(self._mirror(todo).severity, "warning")
		self.assertEqual(self._mirror(todo).state, "Open")

		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "error"})
		self.assertEqual(registry.get_source("ToDo").mirror_levels, frozenset({"error"}))
		self._save_todo(todo, description="changed")

		entry = self._mirror(todo)
		self.assertEqual(entry.state, "Resolved")
		self.assertTrue(entry.resolved_automatically)

	def test_onload_adds_missing_rows_with_app_default(self):
		self._set_rows({"source_doctype": "Note", "enabled": 0, "source": "Old", "app_default": "x"})
		settings = self._settings()
		settings.run_method("onload")

		rows = {row.source_doctype: row for row in settings.sources}
		todo = rows["ToDo"]
		self.assertEqual(todo.enabled, 1)
		self.assertFalse(todo.min_severity)
		self.assertEqual(todo.source, "Test Source")
		self.assertEqual(todo.app_default, "error, warning")
		# Rows of sources no longer registered stay untouched
		self.assertEqual(rows["Note"].source, "Old")
		self.assertEqual(rows["Note"].app_default, "x")
		# Side-effect free: nothing saved
		self.assertEqual(frappe.db.count(ROW, {"parent": SETTINGS}), 1)

	def test_onload_refreshes_existing_rows_and_shows_hook_levels(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 1, "min_severity": "info", "source": "Old"})
		settings = self._settings()
		settings.run_method("onload")
		self.assertEqual(len(settings.sources), 1)
		self.assertEqual(settings.sources[0].source, "Test Source")
		# App default shows the hook levels, not the overridden ones
		self.assertEqual(settings.sources[0].app_default, "error, warning")

	def test_registry_without_child_table(self):
		self._set_rows({"source_doctype": "ToDo", "enabled": 0, "min_severity": "error"})
		table_exists = frappe.db.table_exists

		def missing_child_table(doctype, *args, **kwargs):
			return False if doctype == ROW else table_exists(doctype, *args, **kwargs)

		registry.clear_cache()
		with patch.object(frappe.db, "table_exists", side_effect=missing_child_table):
			self.assertEqual(registry._source_settings(), {})
			config = registry.get_source("ToDo")
		self.assertTrue(config.enabled)
		self.assertEqual(config.mirror_levels, registry.DEFAULT_MIRROR_LEVELS)

		registry.clear_cache()
		with patch.object(frappe, "get_all", side_effect=RuntimeError("database error")):
			self.assertEqual(registry._source_settings(), {})
			config = registry.get_source("ToDo")
		self.assertTrue(config.enabled)
		self.assertEqual(config.mirror_levels, registry.DEFAULT_MIRROR_LEVELS)

	def test_backfill_skips_disabled(self):
		with patch.object(mirror, "_apply", return_value=None):
			todo = self._todo()
		self._set_rows({"source_doctype": "ToDo", "enabled": 0})
		self.assertEqual(api.backfill("ToDo"), 0)
		api.backfill()
		self.assertIsNone(self._mirror(todo))
		with (
			patch("integration_log.api.is_job_enqueued", return_value=False),
			patch("frappe.enqueue") as enqueue,
		):
			self.assertEqual(api.start_backfill(), {"enqueued": [], "already_queued": []})
			self.assertEqual(api.start_backfill("ToDo"), {"enqueued": [], "already_queued": []})
		enqueue.assert_not_called()

	def test_save_drops_rows_of_missing_doctypes(self):
		self._set_rows(
			{"source_doctype": "Integration Log No Such DocType", "enabled": 1},
			{"source_doctype": "ToDo", "enabled": 0},
			{"source_doctype": None, "enabled": 1},
			{"source_doctype": "ToDo", "enabled": 1, "min_severity": "info"},
		)
		rows = frappe.get_all(ROW, filters={"parent": SETTINGS}, fields=["source_doctype", "enabled"])
		self.assertEqual([(r.source_doctype, r.enabled) for r in rows], [("ToDo", 0)])
		self.assertFalse(registry.get_source("ToDo").enabled)

	def test_failed_settings_read_is_not_cached(self):
		registry.clear_cache()
		with patch.object(frappe, "get_all", side_effect=RuntimeError("database error")):
			self.assertTrue(registry.get_source("ToDo").enabled)
		self.assertIsNone(frappe.cache.get_value(registry.CACHE_KEY))

		# A missing table is a normal state (before migrate): that registry is cached
		registry.clear_cache()
		table_exists = frappe.db.table_exists
		with patch.object(
			frappe.db,
			"table_exists",
			side_effect=lambda doctype, *a, **kw: doctype != ROW and table_exists(doctype, *a, **kw),
		):
			registry.get_source("ToDo")
		self.assertIsNotNone(frappe.cache.get_value(registry.CACHE_KEY))

	def test_settings_save_takes_effect_without_manual_cache_clear(self):
		self.assertTrue(registry.get_source("ToDo").enabled)
		self._set_rows({"source_doctype": "ToDo", "enabled": 0})
		self.assertFalse(registry.get_source("ToDo").enabled)
		self._set_rows({"source_doctype": "ToDo", "enabled": 1})
		self.assertTrue(registry.get_source("ToDo").enabled)
