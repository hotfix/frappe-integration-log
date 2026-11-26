import frappe
from frappe.model.document import Document

from integration_log import registry


class IntegrationLogSettings(Document):
	def onload(self):
		"""List every registered source in ``sources`` (not saved: rows only persist when the
		user saves the form). Rows of sources no longer registered are left as they are."""
		sources = registry.get_registry()
		listed = {row.source_doctype for row in self.sources}
		for doctype in sources:
			if doctype not in listed:
				self.append("sources", {"source_doctype": doctype, "enabled": 1, "min_severity": None})
		for row in self.sources:
			config = sources.get(row.source_doctype)
			if config:
				row.source = config.source
				row.app_default = registry.format_levels(config.app_levels)

	def on_update(self):
		registry.clear_cache()
		# Another worker may rebuild from the old value before this commits; clear again after
		frappe.db.after_commit.add(registry.clear_cache)
