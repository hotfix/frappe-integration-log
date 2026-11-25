"""Whitelisted actions on Integration Log Entry, plus backfill of existing source rows."""

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime

from integration_log import mirror, registry

ENTRY = "Integration Log Entry"
SETTINGS = "Integration Log Settings"
BACKFILL_CHUNK = 500


def _check_permission(doctype: str = ENTRY) -> None:
	if not frappe.has_permission(doctype, "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _as_list(names) -> list[str]:
	if isinstance(names, str):
		try:
			parsed = frappe.parse_json(names)
		except Exception:
			parsed = names
		names = parsed
	if isinstance(names, str):
		return [names]
	return [cstr(name) for name in names or []]


@frappe.whitelist()
def acknowledge(names: str | list[str], note: str | None = None) -> int:
	_check_permission()
	count = 0
	for name in _as_list(names):
		entry = frappe.get_doc(ENTRY, name)
		if entry.state != "Open":
			continue
		entry.state = "Acknowledged"
		entry.acknowledged_by = frappe.session.user
		entry.acknowledged_on = now_datetime()
		if note and note.strip():
			entry.note = f"{entry.note}\n{note.strip()}" if entry.note else note.strip()
		entry.save(ignore_permissions=True)
		count += 1
	return count


@frappe.whitelist()
def reopen(names: str | list[str]) -> int:
	_check_permission()
	count = 0
	for name in _as_list(names):
		entry = frappe.get_doc(ENTRY, name)
		if entry.state not in ("Acknowledged", "Resolved"):
			continue
		entry.state = "Open"
		entry.acknowledged_by = None
		entry.acknowledged_on = None
		entry.resolved_automatically = 0
		entry.save(ignore_permissions=True)
		count += 1
	return count


def retry_available(source_doctype: str, source_name: str) -> bool:
	"""Permission-free check whether the source offers a retry for this row."""
	config = registry.get_source(source_doctype)
	if config is None or not config.retry_method:
		return False
	if not config.can_retry_method:
		return True
	try:
		return bool(frappe.get_attr(config.can_retry_method)(source_name))
	except Exception:
		return False


@frappe.whitelist()
def can_retry(name: str) -> bool:
	_check_permission()
	entry = frappe.get_doc(ENTRY, name)
	return retry_available(entry.source_doctype, entry.source_name)


@frappe.whitelist()
def retry(name: str) -> dict:
	_check_permission()
	entry = frappe.get_doc(ENTRY, name)
	config = registry.get_source(entry.source_doctype)
	if config is None or not config.retry_method:
		return {"ok": False, "message": _("This source does not support retry.")}

	message_log = getattr(frappe.local, "message_log", None)
	message_count = len(message_log) if message_log is not None else 0
	try:
		frappe.get_attr(config.retry_method)(entry.source_name)
	except Exception as e:
		if message_log is not None:
			del message_log[message_count:]
		return {"ok": False, "message": cstr(e) or type(e).__name__}

	mirror.sync(entry.source_doctype, entry.source_name)
	return {"ok": True, "message": _("Retry triggered.")}


@frappe.whitelist()
def start_backfill(source_doctype: str | None = None) -> None:
	_check_permission(SETTINGS)
	doctypes = [source_doctype] if source_doctype else sorted(registry.registered_doctypes())
	for doctype in doctypes:
		frappe.enqueue(
			"integration_log.api.backfill",
			queue="long",
			deduplicate=True,
			job_id=f"integration_log_backfill::{frappe.local.site}::{doctype}",
			source_doctype=doctype,
		)


def backfill(source_doctype: str | None = None, chunk: int = BACKFILL_CHUNK) -> int:
	"""Mirror existing source rows (all registered sources when none given). Idempotent."""
	doctypes = [source_doctype] if source_doctype else sorted(registry.registered_doctypes())
	processed = 0
	for doctype in doctypes:
		if registry.get_source(doctype) is None:
			continue
		start = 0
		while True:
			names = frappe.get_all(
				doctype, pluck="name", order_by="creation asc, name asc", limit_start=start, limit=chunk
			)
			for name in names:
				mirror.sync(doctype, name)
			frappe.db.commit()
			processed += len(names)
			if len(names) < chunk:
				break
			start += chunk
	return processed
