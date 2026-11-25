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

	def test_german_translations(self):
		frappe.local.lang = "de"
		try:
			self.assertEqual(frappe._("Acknowledge"), "Erledigt")
			self.assertEqual(frappe._("Reopen"), "Wieder öffnen")
			self.assertEqual(frappe._("Retry"), "Erneut versuchen")
			self.assertEqual(frappe._("Integration Log"), "Integrations-Protokoll")
		finally:
			frappe.local.lang = "en"
