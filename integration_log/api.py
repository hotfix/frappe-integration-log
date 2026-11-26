"""Whitelisted actions on Integration Log Entry, plus backfill of existing source rows."""

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

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
			parsed = None
		if not isinstance(parsed, list):
			return [names]
		names = parsed
	return [cstr(name) for name in names or []]


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
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


@frappe.whitelist(methods=["POST"])
def retry(name: str) -> dict:
	"""Run the source's retry_method for the entry's source row, then re-sync the mirror.

	On failure the open transaction is rolled back, so a partial write by the consumer
	is not committed with the request (anything the consumer committed itself stays)."""
	_check_permission()
	entry = frappe.get_doc(ENTRY, name)
	config = registry.get_source(entry.source_doctype)
	if config is None or not config.retry_method:
		return {"ok": False, "message": _("This source does not support retry.")}

	if not retry_available(entry.source_doctype, entry.source_name):
		return {"ok": False, "message": _("Retry is not available for this entry.")}

	message_log = getattr(frappe.local, "message_log", None)
	message_count = len(message_log) if message_log is not None else 0
	try:
		frappe.get_attr(config.retry_method)(entry.source_name)
	except Exception as e:
		frappe.db.rollback()
		if message_log is not None:
			del message_log[message_count:]
		return {"ok": False, "message": cstr(e) or type(e).__name__}

	mirror.sync(entry.source_doctype, entry.source_name)
	return {"ok": True, "message": _("Retry triggered.")}


@frappe.whitelist(methods=["POST"])
def start_backfill(source_doctype: str | None = None) -> list[str]:
	"""Enqueue backfill jobs; returns the doctypes enqueued (already queued ones are skipped)."""
	_check_permission(SETTINGS)
	doctypes = [source_doctype] if source_doctype else sorted(registry.registered_doctypes())
	enqueued = []
	for doctype in doctypes:
		config = registry.get_source(doctype)
		if config is not None and not config.enabled:
			continue
		# Frappe namespaces the job id by site itself. enqueue() returns None with
		# enqueue_after_commit, so deduplicate by asking the queue ourselves.
		job_id = f"integration_log_backfill::{doctype}"
		if is_job_enqueued(job_id):
			continue
		frappe.enqueue(
			"integration_log.api.backfill",
			queue="long",
			deduplicate=True,
			enqueue_after_commit=True,
			job_id=job_id,
			source_doctype=doctype,
		)
		enqueued.append(doctype)
	return enqueued


def backfill(source_doctype: str | None = None, chunk: int = BACKFILL_CHUNK) -> int:
	"""Mirror existing source rows (all registered sources when none given). Idempotent.

	Disabled sources are skipped."""
	doctypes = [source_doctype] if source_doctype else sorted(registry.registered_doctypes())
	processed = 0
	for doctype in doctypes:
		config = registry.get_source(doctype)
		if config is None or not config.enabled:
			continue
		if not frappe.db.table_exists(doctype):
			continue
		table = frappe.qb.DocType(doctype)
		last = None
		while True:
			query = (
				frappe.qb.from_(table)
				.select(table.name, table.creation)
				.orderby(table.creation)
				.orderby(table.name)
				.limit(chunk)
			)
			if last:
				query = query.where(
					(table.creation > last[0]) | ((table.creation == last[0]) & (table.name > last[1]))
				)
			rows = query.run()
			for name, _creation in rows:
				mirror.sync(doctype, name)
			frappe.db.commit()
			processed += len(rows)
			if len(rows) < chunk:
				break
			last = (rows[-1][1], rows[-1][0])
	return processed
