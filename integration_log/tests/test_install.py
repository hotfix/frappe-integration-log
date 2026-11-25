from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase


class TestInstall(IntegrationTestCase):
	def test_role_exists(self):
		self.assertTrue(frappe.db.exists("Role", "Integration Log Manager"))

	def test_unique_index_on_source(self):
		def make():
			return frappe.get_doc(
				{
					"doctype": "Integration Log Entry",
					"state": "Open",
					"severity": "error",
					"source": "Test",
					"title": "t",
					"source_doctype": "ToDo",
					"source_name": "unique-index-test",
				}
			).insert(ignore_permissions=True, ignore_links=True)

		make()
		try:
			with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
				make()
		finally:
			frappe.db.rollback()

	def test_settings_defaults(self):
		settings = frappe.get_single("Integration Log Settings")
		self.assertEqual(settings.retention_days, 90)
		self.assertEqual(settings.include_error_log, 0)

	def test_settings_save_clears_registry_cache(self):
		settings = frappe.get_single("Integration Log Settings")
		with patch("integration_log.registry.clear_cache") as clear:
			settings.save()
		clear.assert_called()
