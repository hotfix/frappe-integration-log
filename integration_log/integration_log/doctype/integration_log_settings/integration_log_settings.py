from frappe.model.document import Document

from integration_log import registry


class IntegrationLogSettings(Document):
	def on_update(self):
		registry.clear_cache()
