"""Mirror entries of registered log DocTypes into Integration Log Entry.

Runs from ``doc_events["*"]`` inside the source transaction, so a rollback of the
source also removes its mirror. Mirroring never makes the source save fail: all
writes happen inside a uniquely named savepoint; on any exception the savepoint is
rolled back and the failure is reported once (see ``_report_failure``).
"""

import hashlib
import json
from collections.abc import Callable

import frappe
from frappe.utils import add_to_date, cstr, now_datetime, strip_html_tags

from integration_log import registry
from integration_log.registry import SourceConfig

ENTRY_DOCTYPE = "Integration Log Entry"
MAX_TITLE = 140
MAX_MESSAGE = 10000
MAX_DATA = 140
FAILED_TITLE = "Integration Log mirror failed: {}"
# At most one unseen "mirror failed" Error Log per source DocType and hour
FAILED_DEDUP_HOURS = 1


def on_doc_event(doc, method=None) -> None:
	config = registry.get_source(doc.doctype)
	if config is None:
		return
	_guarded(doc.doctype, lambda: _apply(config, doc))


def on_doc_trash(doc, method=None) -> None:
	if registry.get_source(doc.doctype) is None:
		return
	_guarded(doc.doctype, lambda: _delete_mirror(doc.doctype, doc.name))


def sync(doctype: str, name: str) -> str | None:
	"""Re-read a source row and mirror it (for writes via ``frappe.db.set_value``).

	Returns the name of the mirror entry, or None when there is none."""
	config = registry.get_source(doctype)
	if config is None:
		return None

	def run():
		if not frappe.db.exists(doctype, name):
			_delete_mirror(doctype, name)
			return None
		return _apply(config, frappe.get_doc(doctype, name))

	return _guarded(doctype, run)


def severity_of(config: SourceConfig, doc) -> str:
	return registry.resolve_severity(config, doc)


def group_key_of(config: SourceConfig, doc) -> str | None:
	"""sha1 of the group field values; None without group fields or when all are empty."""
	if not config.group_fields:
		return None
	values = [cstr(doc.get(field)) for field in config.group_fields]
	if not any(values):
		# An empty group would tie together unrelated rows
		return None
	return hashlib.sha1(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def _apply(config: SourceConfig, doc) -> str | None:
	severity = severity_of(config, doc)
	group_key = group_key_of(config, doc)
	existing = frappe.db.get_value(
		ENTRY_DOCTYPE,
		{"source_doctype": doc.doctype, "source_name": doc.name},
		["name", "state", "resolved_automatically"],
		as_dict=True,
	)

	if severity in config.mirror_levels:
		values = _mirror_values(config, doc, severity, group_key)
		if not existing:
			entry = frappe.get_doc({"doctype": ENTRY_DOCTYPE, "state": "Open", **values})
			entry.insert(ignore_permissions=True, ignore_links=True)
			return entry.name
		if existing.state == "Resolved" and existing.resolved_automatically:
			values.update(state="Open", resolved_automatically=0)
		# Acknowledged stays Acknowledged
		_update_entry(existing.name, values)
		return existing.name

	if existing and existing.state == "Open":
		_update_entry(
			existing.name,
			{
				"state": "Resolved",
				"resolved_automatically": 1,
				"source_status": _source_status(config, doc),
				"last_seen": doc.modified,
			},
		)
	if group_key:
		_resolve_group(doc.doctype, group_key, doc.creation)
	return existing.name if existing else None


def _resolve_group(doctype: str, group_key: str, creation) -> None:
	names = frappe.get_all(
		ENTRY_DOCTYPE,
		filters={
			"source_doctype": doctype,
			"group_key": group_key,
			"state": "Open",
			"occurred_on": ("<=", creation),
		},
		pluck="name",
	)
	for name in names:
		_update_entry(name, {"state": "Resolved", "resolved_automatically": 1})


def _mirror_values(config: SourceConfig, doc, severity: str, group_key: str | None) -> dict:
	message = cstr(doc.get(config.message_field))[:MAX_MESSAGE]
	title = cstr(doc.get(config.title_field)).strip() if config.title_field else ""
	values = {
		"severity": severity,
		"source": config.source,
		"title": (title or _first_line(message) or doc.name)[:MAX_TITLE],
		"message": message,
		"source_status": _source_status(config, doc),
		"source_doctype": doc.doctype,
		"source_name": doc.name,
		"group_key": group_key,
		"context": _context(config, doc),
		"occurred_on": doc.creation,
		"last_seen": doc.modified,
	}
	if config.link_fields:
		ref_doctype, ref_name = (doc.get(field) or None for field in config.link_fields)
		values.update(ref_doctype=ref_doctype, ref_name=ref_name)
	return values


def _source_status(config: SourceConfig, doc) -> str | None:
	if not config.status_field:
		return None
	return cstr(doc.get(config.status_field))[:MAX_DATA] or None


def _context(config: SourceConfig, doc) -> str | None:
	if not config.context_fields:
		return None
	return json.dumps(
		{field: doc.get(field) for field in config.context_fields}, default=str, ensure_ascii=False
	)


def _first_line(text: str) -> str:
	for line in strip_html_tags(text).splitlines():
		if line.strip():
			return line.strip()
	return ""


def _update_entry(name: str, values: dict) -> None:
	entry = frappe.get_doc(ENTRY_DOCTYPE, name)
	if all(cstr(entry.get(key)) == cstr(value) for key, value in values.items()):
		return
	entry.update(values)
	entry.flags.ignore_links = True
	entry.save(ignore_permissions=True)


def _delete_mirror(doctype: str, name: str) -> None:
	frappe.db.delete(ENTRY_DOCTYPE, {"source_doctype": doctype, "source_name": name})


def _guarded(doctype: str, fn: Callable):
	"""Run fn in its own savepoint; never raise.

	The savepoint name is unique per call, so this nests safely inside savepoints of
	the calling code (e.g. a consumer rolling back a savepoint per record)."""
	save_point = f"integration_log_mirror_{frappe.generate_hash(length=10)}"
	message_log = getattr(frappe.local, "message_log", None)
	message_count = len(message_log) if message_log is not None else 0
	try:
		frappe.db.savepoint(save_point)
	except Exception:
		_logger().exception(FAILED_TITLE.format(doctype))
		return None

	try:
		result = fn()
	except Exception:
		# Drop messages raised by frappe.throw while mirroring: not the user's concern
		if message_log is not None:
			del message_log[message_count:]
		try:
			frappe.db.rollback(save_point=save_point)
		except Exception:
			_logger().exception(FAILED_TITLE.format(doctype))
			return None
		_report_failure(doctype)
		return None

	try:
		frappe.db.release_savepoint(save_point)
	except Exception:
		_logger().exception(FAILED_TITLE.format(doctype))
	return result


def _report_failure(doctype: str) -> None:
	"""Report a mirror failure; call from inside an ``except`` block (logs the traceback).

	Error Log sources only go to the file logger, so a failure cannot produce another
	Error Log (recursion). Otherwise one Error Log is written unless an unseen one with
	the same title exists from the last FAILED_DEDUP_HOURS."""
	title = FAILED_TITLE.format(doctype)
	_logger().exception(title)
	if doctype == "Error Log":
		return
	try:
		since = add_to_date(now_datetime(), hours=-FAILED_DEDUP_HOURS)
		if frappe.db.exists("Error Log", {"method": title, "seen": 0, "creation": (">", since)}):
			return
		frappe.log_error(title=title)
	except Exception:
		_logger().exception(f"{title} (could not write Error Log)")


def _logger():
	return frappe.logger("integration_log")
