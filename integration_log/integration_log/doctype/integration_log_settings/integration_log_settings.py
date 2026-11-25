import frappe
from frappe.model.document import Document

from integration_log import registry


class IntegrationLogSettings(Document):
	def on_update(self):
		registry.clear_cache()
		# Another worker may rebuild from the old value before this commits; clear again after
		frappe.db.after_commit.add(registry.clear_cache)
