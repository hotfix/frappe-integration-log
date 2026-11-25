import json

import frappe
from frappe.tests import IntegrationTestCase


class TestWorkspace(IntegrationTestCase):
	def test_standard_records_exist(self):
		self.assertTrue(frappe.db.exists("Workspace", "Integration Log"))
		self.assertTrue(frappe.db.exists("Number Card", "Open Errors"))
		self.assertTrue(frappe.db.exists("Number Card", "Open Warnings"))
		self.assertTrue(frappe.db.exists("Dashboard Chart", "Open by Source"))
		self.assertTrue(frappe.db.exists("Report", "Integration Log by Source"))

	def test_number_card_filters(self):
		for name, severity in (("Open Errors", "error"), ("Open Warnings", "warning")):
			filters = json.loads(frappe.db.get_value("Number Card", name, "filters_json"))
			flat = [tuple(f[1:4]) for f in filters]
			self.assertIn(("state", "=", "Open"), flat)
			self.assertIn(("severity", "=", severity), flat)

	def test_chart_groups_by_source(self):
		chart = frappe.get_doc("Dashboard Chart", "Open by Source")
		self.assertEqual(chart.chart_type, "Group By")
		self.assertEqual(chart.group_by_based_on, "source")

	def test_workspace_content(self):
		ws = frappe.get_doc("Workspace", "Integration Log")
		self.assertEqual({c.number_card_name for c in ws.number_cards}, {"Open Errors", "Open Warnings"})
		self.assertEqual([c.chart_name for c in ws.charts], ["Open by Source"])
		self.assertEqual(
			{s.link_to for s in ws.shortcuts},
			{"Integration Log Entry", "Integration Log Settings", "Integration Log by Source"},
		)

	def test_sidebar_shipped(self):
		# In v16.55 the module-keyed Sidebar is the live navigation store; the Workspace
		# Sidebar doctype is an inert archive, so no Workspace Sidebar is shipped.
		self.assertTrue(frappe.db.exists("Sidebar", "Integration Log"))
		sidebar = frappe.get_doc("Sidebar", "Integration Log")
		self.assertEqual(
			(sidebar.module, sidebar.app, sidebar.standard), ("Integration Log", "integration_log", 1)
		)
		expected = {
			"Integration Log",
			"Integration Log Entry",
			"Integration Log Settings",
			"Integration Log by Source",
		}
		self.assertTrue(expected <= {item.link_to for item in sidebar.items})

	def test_apps_screen_tile(self):
		from frappe.apps import get_apps

		hooks = frappe.get_hooks("add_to_apps_screen", app_name="integration_log")
		self.assertEqual(hooks[0]["route"], "/desk/integration-log")
		self.assertIn("integration_log", [a["name"] for a in get_apps()])

	def test_desktop_icon_fixture(self):
		path = frappe.get_app_path("integration_log", "desktop_icon", "integration_log.json")
		with open(path) as f:
			data = json.load(f)
		self.assertEqual(
			(data["name"], data["link_type"], data["link_to"], data["app"], data["standard"]),
			("Integration Log", "Workspace Sidebar", "Integration Log", "integration_log", 1),
		)

	def test_desktop_icon_synced_in_icon_mode_and_idempotent(self):
		# Icon rows exist only when the site is in "Desktop Icons" mode, so force the guard.
		from unittest.mock import patch

		from frappe.model.sync import sync_for

		self.addCleanup(self._remove_icon)
		with patch("frappe.desk.doctype.desktop_icon.desktop_icon.is_desktop_icons_page", return_value=True):
			sync_for("integration_log", force=True)
			sync_for("integration_log", force=True)
		self.assertEqual(frappe.db.count("Desktop Icon", {"name": "Integration Log"}), 1)
		self.assertEqual(
			frappe.db.get_value(
				"Desktop Icon", "Integration Log", ["link_type", "link_to", "app"], as_dict=True
			),
			{"link_type": "Workspace Sidebar", "link_to": "Integration Log", "app": "integration_log"},
		)
		# sidebar untouched by repeated sync
		self.assertEqual(frappe.db.count("Sidebar", {"name": "Integration Log"}), 1)
		self.assertEqual(len(frappe.get_doc("Sidebar", "Integration Log").items), 5)

	def _remove_icon(self):
		if frappe.db.exists("Desktop Icon", "Integration Log"):
			# db.delete, not delete_doc: in developer mode delete_doc removes the shipped fixture file
			frappe.db.delete("Desktop Icon", {"name": "Integration Log"})
			frappe.db.commit()
			frappe.clear_cache()

	def test_german_translations(self):
		frappe.local.lang = "de"
		try:
			self.assertEqual(frappe._("Acknowledge"), "Erledigt")
			self.assertEqual(frappe._("Reopen"), "Wieder öffnen")
			self.assertEqual(frappe._("Retry"), "Erneut versuchen")
			self.assertEqual(frappe._("Integration Log"), "Integrations-Protokoll")
		finally:
			frappe.local.lang = "en"
