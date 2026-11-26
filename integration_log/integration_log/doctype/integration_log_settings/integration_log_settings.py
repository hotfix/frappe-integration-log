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

	def validate(self):
		self._clean_sources()

	def _validate_links(self):
		# Frappe validates links before validate(): drop rows of DocTypes that no longer exist
		# (e.g. the consumer app was uninstalled) first, so they cannot block the save
		self._clean_sources()
		super()._validate_links()

	def _clean_sources(self):
		"""Keep one row per existing source DocType (the first); drop blank and stale rows."""
		seen = set()
		rows = []
		for row in self.sources:
			doctype = row.source_doctype
			if not doctype or doctype in seen or not frappe.db.exists("DocType", doctype):
				continue
			seen.add(doctype)
			rows.append(row)
		if len(rows) != len(self.sources):
			for idx, row in enumerate(rows, start=1):
				row.idx = idx
			self.set("sources", rows)

	def on_update(self):
		registry.clear_cache()
		# Another worker may rebuild from the old value before this commits; clear again after
		frappe.db.after_commit.add(registry.clear_cache)
