import frappe
from frappe.model.document import Document


class IntegrationLogEntry(Document):
	def onload(self):
		from integration_log.api import retry_available

		can_retry = frappe.has_permission(self.doctype, "write") and retry_available(
			self.source_doctype, self.source_name
		)
		self.set_onload("can_retry", bool(can_retry))
