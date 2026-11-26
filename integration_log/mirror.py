"""Mirror entries of registered log DocTypes into Integration Log Entry.

Runs from ``doc_events["*"]`` (``on_change``, which also fires for ``db_set``, and
``on_trash``) inside the source transaction, so a rollback of the source also removes
its mirror. All writes happen inside a uniquely named savepoint; on an exception the
savepoint is rolled back and the failure is reported once (see ``_report_failure``),
so mirroring does not make the source save fail.

The one exception: when the transaction itself is lost (a deadlock, or the rollback
to the savepoint fails), the original exception is re-raised. Swallowing it would let
the caller commit a transaction whose earlier writes are already gone.
"""

import hashlib
import json
from collections.abc import Callable

import frappe
from frappe.utils import add_to_date, cstr, get_datetime, now_datetime, strip_html_tags

from integration_log import registry
from integration_log.registry import SourceConfig

ENTRY_DOCTYPE = "Integration Log Entry"
MAX_TITLE = 140
MAX_MESSAGE = 10000
MAX_DATA = 140
FAILED_TITLE = "Integration Log mirror failed: {}"
# At most one unseen "mirror failed" Error Log per source DocType and hour
FAILED_DEDUP_HOURS = 1
DATETIME_FIELDS = frozenset({"occurred_on", "last_seen"})


def on_doc_event(doc, method=None) -> None:
	if doc.flags.in_delete:
		# delete_doc fires on_change right after on_trash; do not recreate the mirror
		return
	config = _get_source(doc.doctype)
	if config is None or not config.enabled:
		return
	_guarded(doc.doctype, lambda: _apply(config, doc))


def on_doc_trash(doc, method=None) -> None:
	# Also for a disabled source: a mirror never outlives its source row
	if _get_source(doc.doctype) is None:
		return
	_guarded(doc.doctype, lambda: _delete_mirror(doc.doctype, doc.name))


def _get_source(doctype: str) -> SourceConfig | None:
	"""Registry lookup that never raises: it runs for every document save on the site."""
	try:
		return registry.get_source(doctype)
	except Exception:
		_logger().exception(f"Integration Log: registry lookup failed for {doctype}")
		return None


def sync(doctype: str, name: str) -> str | None:
	"""Re-read a source row and mirror it (for writes via ``frappe.db.set_value``).

	Returns the name of the mirror entry, or None when there is none. For a disabled
	source the existing mirror is left as it is (deleted only when the row is gone)."""
	config = _get_source(doctype)
	if config is None:
		return None

	def run():
		if not frappe.db.exists(doctype, name):
			_delete_mirror(doctype, name)
			return None
		if not config.enabled:
			return frappe.db.get_value(ENTRY_DOCTYPE, {"source_doctype": doctype, "source_name": name})
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
		["name", "state", "resolved_automatically", "last_seen"],
		as_dict=True,
	)

	if severity in config.mirror_levels:
		values = _mirror_values(config, doc, severity, group_key)
		if not existing:
			entry = frappe.get_doc({"doctype": ENTRY_DOCTYPE, "state": "Open", **values})
			entry.insert(ignore_permissions=True, ignore_links=True)
			return entry.name
		if existing.state == "Resolved" and existing.resolved_automatically and _changed_since(doc, existing):
			# Only a real change of the row reopens; re-applying an unchanged row (backfill,
			# sync, an unrelated update) must not undo a resolution by its group
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


def _changed_since(doc, existing) -> bool:
	if not existing.last_seen:
		return True
	return get_datetime(doc.modified) > get_datetime(existing.last_seen)


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
		"area": _area(config, doc),
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


def _area(config: SourceConfig, doc) -> str | None:
	if not config.area_field:
		return None
	return cstr(doc.get(config.area_field)).strip()[:MAX_DATA] or None


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
	if all(_same(key, entry.get(key), value) for key, value in values.items()):
		return
	entry.update(values)
	entry.flags.ignore_links = True
	entry.save(ignore_permissions=True)


def _same(key: str, current, new) -> bool:
	if key in DATETIME_FIELDS:
		return (get_datetime(current) if current else None) == (get_datetime(new) if new else None)
	return cstr(current) == cstr(new)


def _delete_mirror(doctype: str, name: str) -> None:
	frappe.db.delete(ENTRY_DOCTYPE, {"source_doctype": doctype, "source_name": name})


def _guarded(doctype: str, fn: Callable):
	"""Run fn in its own savepoint; swallow and report its exceptions.

	Re-raises the original exception when the transaction is lost: on a deadlock, and
	when the rollback to the savepoint fails. Continuing would let the caller commit a
	half-lost transaction.

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
	except Exception as e:
		if _is_deadlock(e):
			# InnoDB rolled back the whole transaction, savepoints included
			_logger().exception(FAILED_TITLE.format(doctype))
			raise
		# Drop messages raised by frappe.throw while mirroring: not the user's concern
		if message_log is not None:
			del message_log[message_count:]
		try:
			frappe.db.rollback(save_point=save_point)
		except Exception:
			_logger().exception(FAILED_TITLE.format(doctype))
			raise e from None
		_report_failure(doctype)
		return None

	try:
		frappe.db.release_savepoint(save_point)
	except Exception:
		_logger().exception(FAILED_TITLE.format(doctype))
	return result


def _is_deadlock(e: Exception) -> bool:
	if isinstance(e, frappe.QueryDeadlockError):
		return True
	try:
		return bool(frappe.db.is_deadlocked(e))
	except Exception:
		return False


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
