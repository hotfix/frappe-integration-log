from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime

from integration_log import api, mirror, registry, tasks
from integration_log.tests.test_mirror import todo_entry

ENTRY = "Integration Log Entry"
SETTINGS = "Integration Log Settings"
HELPERS = "integration_log.tests.helpers"
NO_ROLE_USER = "il-norole@example.com"


class TestApi(IntegrationTestCase):
	def setUp(self):
		self._register([todo_entry(retry_method=f"{HELPERS}.close_todo")])

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.set_single_value(SETTINGS, "retention_days", 90)
		frappe.clear_document_cache(SETTINGS)
		registry.clear_cache()

	def _register(self, entries):
		registry.clear_cache()
		with patch.object(registry, "_load_hook_entries", return_value=entries):
			registry.get_registry()

	def _todo(self, description="Sync failed", status="Open", ref="Administrator"):
		return frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": description,
				"status": status,
				"reference_type": "User" if ref else None,
				"reference_name": ref,
			}
		).insert(ignore_permissions=True)

	def _entry(self, todo):
		name = frappe.db.get_value(ENTRY, {"source_doctype": "ToDo", "source_name": todo.name})
		return frappe.get_doc(ENTRY, name)

	def _new(self, **kwargs):
		return self._entry(self._todo(ref=None, **kwargs))

	def test_acknowledge_sets_fields_and_count(self):
		a, b = self._new(), self._new()
		self.assertEqual(api.acknowledge([a.name, b.name], note="seen"), 2)
		a.reload()
		self.assertEqual(a.state, "Acknowledged")
		self.assertEqual(a.acknowledged_by, "Administrator")
		self.assertIsNotNone(a.acknowledged_on)
		self.assertEqual(a.note, "seen")
		self.assertEqual(api.acknowledge(b.name), 0)  # single name as str, already acknowledged

	def test_acknowledge_json_string_names(self):
		a = self._new()
		self.assertEqual(api.acknowledge(frappe.as_json([a.name])), 1)

	def test_acknowledge_ignores_non_open(self):
		a = self._new()
		a.state = "Resolved"
		a.save(ignore_permissions=True)
		self.assertEqual(api.acknowledge(a.name, note="x"), 0)
		a.reload()
		self.assertEqual(a.state, "Resolved")
		self.assertFalse(a.note)

	def test_acknowledge_appends_note(self):
		a = self._new()
		a.note = "first"
		a.save(ignore_permissions=True)
		api.acknowledge(a.name, note="second")
		a.reload()
		self.assertEqual(a.note, "first\nsecond")

	def test_reopen_clears_fields(self):
		a, b = self._new(), self._new()
		api.acknowledge(a.name)
		b.state = "Resolved"
		b.resolved_automatically = 1
		b.save(ignore_permissions=True)
		self.assertEqual(api.reopen([a.name, b.name]), 2)
		for entry in (a, b):
			entry.reload()
			self.assertEqual(entry.state, "Open")
			self.assertFalse(entry.acknowledged_by)
			self.assertFalse(entry.acknowledged_on)
			self.assertEqual(entry.resolved_automatically, 0)
		self.assertEqual(api.reopen(a.name), 0)

	def test_permission_required(self):
		entry = self._new()
		if not frappe.db.exists("User", NO_ROLE_USER):
			frappe.get_doc(
				{"doctype": "User", "email": NO_ROLE_USER, "first_name": "NoRole", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.set_user(NO_ROLE_USER)
		for call in (
			lambda: api.acknowledge(entry.name),
			lambda: api.reopen(entry.name),
			lambda: api.retry(entry.name),
			lambda: api.can_retry(entry.name),
		):
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_retry_calls_source_method_and_syncs(self):
		entry = self._entry(self._todo())
		self.assertTrue(api.can_retry(entry.name))
		result = api.retry(entry.name)
		self.assertTrue(result["ok"])
		entry.reload()
		self.assertEqual(entry.state, "Resolved")

	def test_retry_failure_returns_message(self):
		self._register([todo_entry(retry_method=f"{HELPERS}.fail_retry")])
		todo = self._todo()
		entry = self._entry(todo)
		frappe.db.commit()  # a failed retry rolls back the open transaction
		try:
			self.assertEqual(api.retry(entry.name), {"ok": False, "message": "nope"})
			entry.reload()
			self.assertEqual(entry.state, "Open")
		finally:
			frappe.db.delete(ENTRY, {"name": entry.name})
			frappe.db.delete("ToDo", {"name": todo.name})
			frappe.db.commit()

	def test_retry_respects_can_retry_method(self):
		self._register(
			[
				todo_entry(
					retry_method=f"{HELPERS}.close_todo",
					can_retry_method=f"{HELPERS}.can_retry_only_for_marked",
				)
			]
		)
		todo = self._todo(description="other")
		entry = self._entry(todo)
		result = api.retry(entry.name)
		self.assertFalse(result["ok"])
		self.assertEqual(frappe.db.get_value("ToDo", todo.name, "status"), "Open")

	def test_retry_failure_rolls_back_partial_write(self):
		self._register([todo_entry(retry_method=f"{HELPERS}.write_then_fail")])
		todo = self._todo()
		entry = self._entry(todo)
		frappe.db.commit()
		try:
			result = api.retry(entry.name)
			self.assertEqual(result, {"ok": False, "message": "late failure"})
			self.assertEqual(frappe.db.get_value("ToDo", todo.name, "description"), "Sync failed")
		finally:
			frappe.db.delete(ENTRY, {"name": entry.name})
			frappe.db.delete("ToDo", {"name": todo.name})
			frappe.db.commit()

	def test_as_list_non_list_json_is_single_name(self):
		self.assertEqual(api._as_list("123"), ["123"])
		self.assertEqual(api._as_list("null"), ["null"])
		self.assertEqual(api._as_list('["a", "b"]'), ["a", "b"])

	def test_retry_without_method(self):
		self._register([todo_entry()])
		entry = self._entry(self._todo())
		self.assertFalse(api.can_retry(entry.name))
		self.assertFalse(api.retry(entry.name)["ok"])

	def test_can_retry_uses_name(self):
		self._register(
			[
				todo_entry(
					retry_method=f"{HELPERS}.close_todo",
					can_retry_method=f"{HELPERS}.can_retry_only_for_marked",
				)
			]
		)
		yes = self._entry(self._todo(description="retryable"))
		no = self._entry(self._todo(description="other"))
		self.assertTrue(api.can_retry(yes.name))
		self.assertFalse(api.can_retry(no.name))

	def test_onload_sets_can_retry(self):
		entry = self._entry(self._todo())
		entry.run_method("onload")
		self.assertTrue(entry.get_onload("can_retry"))

	def test_start_backfill_enqueues(self):
		with patch("frappe.enqueue") as enqueue:
			self.assertEqual(api.start_backfill(), ["ToDo"])
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])
		self.assertEqual(enqueue.call_args.args[0], "integration_log.api.backfill")
		self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
		self.assertTrue(enqueue.call_args.kwargs["deduplicate"])
		self.assertTrue(enqueue.call_args.kwargs["job_id"])

	def test_backfill_idempotent(self):
		with patch.object(mirror, "_apply", return_value=None):
			names = [self._todo(ref=None).name for _ in range(3)]
		filters = {"source_doctype": "ToDo", "source_name": ("in", names)}
		self.assertEqual(frappe.db.count(ENTRY, filters), 0)
		api.backfill("ToDo", chunk=2)
		self.assertEqual(frappe.db.count(ENTRY, filters), 3)
		api.backfill("ToDo", chunk=2)
		self.assertEqual(frappe.db.count(ENTRY, filters), 3)

	def test_start_backfill_reports_deduplicated(self):
		with patch("frappe.enqueue", return_value=None):
			self.assertEqual(api.start_backfill(), [])

	def test_after_install_survives_enqueue_failure(self):
		from integration_log import install

		title = "Integration Log: backfill could not be started"
		before = frappe.db.count("Error Log", {"method": title})
		with (
			patch.object(registry, "_load_hook_entries", return_value=[todo_entry()]),
			patch("frappe.enqueue", side_effect=ConnectionError("redis down")),
		):
			install.after_install()
		self.assertEqual(frappe.db.count("Error Log", {"method": title}), before + 1)

	def _aged(self, entry, state, days):
		entry.state = state
		entry.save(ignore_permissions=True)
		frappe.db.set_value(
			ENTRY, entry.name, "modified", add_days(now_datetime(), -days), update_modified=False
		)
		return entry.name

	def test_cleanup_respects_retention(self):
		old_resolved = self._aged(self._new(), "Resolved", 100)
		old_ack = self._aged(self._new(), "Acknowledged", 100)
		old_open = self._aged(self._new(), "Open", 100)
		new_resolved = self._aged(self._new(), "Resolved", 10)
		tasks.cleanup_done()
		self.assertFalse(frappe.db.exists(ENTRY, old_resolved))
		self.assertFalse(frappe.db.exists(ENTRY, old_ack))
		self.assertTrue(frappe.db.exists(ENTRY, old_open))
		self.assertTrue(frappe.db.exists(ENTRY, new_resolved))

		frappe.db.set_single_value(SETTINGS, "retention_days", 0)
		frappe.clear_document_cache(SETTINGS)
		again = self._aged(self._new(), "Resolved", 1000)
		tasks.cleanup_done()
		self.assertTrue(frappe.db.exists(ENTRY, again))

	def test_cleanup_removes_orphans(self):
		orphan_todo = self._todo(ref=None)
		kept_todo = self._todo(ref=None)
		orphan, kept = self._entry(orphan_todo).name, self._entry(kept_todo).name
		frappe.db.delete("ToDo", {"name": orphan_todo.name})
		tasks.cleanup_done()
		self.assertFalse(frappe.db.exists(ENTRY, orphan))
		self.assertTrue(frappe.db.exists(ENTRY, kept))
