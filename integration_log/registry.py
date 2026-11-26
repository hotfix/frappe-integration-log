"""Registry of integration log sources.

Apps declare their log DocType in hooks.py::

        integration_logs = [
            {
                "doctype": "My Sync Log",
                "source": "My App",
                "status_field": "status",
                "severity_map": {"Failed": "error", "Skipped": "warning", "Done": "info"},
                "message_field": "error_message",
            }
        ]

Integration Log Settings has one row per source (``sources``): ``enabled`` and
``min_severity`` override the hook entry. A source without a row keeps the hook values.
"""

from dataclasses import dataclass, replace

import frappe
from frappe.model import default_fields

SEVERITIES = ("info", "warning", "error")
DEFAULT_MIRROR_LEVELS = frozenset({"error", "warning"})
INVALID_ENTRY_TITLE = "Integration Log: invalid registry entry"
SETTINGS_DOCTYPE = "Integration Log Settings"
ENTRY_DOCTYPE = "Integration Log Entry"
SOURCE_SETTING_DOCTYPE = "Integration Log Source Setting"
# Mirroring these into themselves would recurse
OWN_DOCTYPES = frozenset({ENTRY_DOCTYPE, SETTINGS_DOCTYPE, SOURCE_SETTING_DOCTYPE})
CACHE_KEY = "integration_log_registry"
LOCAL_ATTR = "integration_log_registry"
BUILDING_ATTR = "integration_log_registry_building"


def severity_rank(severity: str) -> int:
	return SEVERITIES.index(severity)


def effective_levels(min_severity: str) -> frozenset[str]:
	"""All severities at or above min_severity."""
	rank = severity_rank(min_severity)
	return frozenset(s for s in SEVERITIES if severity_rank(s) >= rank)


def format_levels(levels: frozenset[str]) -> str:
	"""Levels as text, most severe first, e.g. "error, warning"."""
	return ", ".join(s for s in reversed(SEVERITIES) if s in levels)


@dataclass(frozen=True)
class SourceConfig:
	doctype: str
	source: str
	status_field: str | None
	severity_map: dict[str, str]
	message_field: str
	title_field: str | None = None
	context_fields: tuple[str, ...] = ()
	group_fields: tuple[str, ...] = ()
	link_fields: tuple[str, str] | None = None
	mirror_levels: frozenset[str] = DEFAULT_MIRROR_LEVELS
	retry_method: str | None = None
	can_retry_method: str | None = None
	severity_method: str | None = None
	# From the source's settings row: a disabled source is kept in the registry (so the
	# settings can list it) but not mirrored
	enabled: bool = True
	# The hook's mirror levels, before a settings row's min_severity replaced them
	app_levels: frozenset[str] = DEFAULT_MIRROR_LEVELS


ERROR_LOG_SOURCE = SourceConfig(
	doctype="Error Log",
	source="System",
	status_field=None,
	severity_map={},
	message_field="error",
	title_field="method",
	link_fields=("reference_doctype", "reference_name"),
)


def _load_hook_entries() -> list[dict]:
	return frappe.get_hooks("integration_logs") or []


def resolve_severity(config: SourceConfig, doc) -> str:
	"""Severity of a log document: severity_method wins, then severity_map, else warning."""
	if config.severity_method:
		severity = frappe.get_attr(config.severity_method)(doc)
		return severity if severity in SEVERITIES else "warning"
	if not config.status_field:
		return "error"
	return config.severity_map.get(doc.get(config.status_field), "warning")


def _include_error_log() -> bool:
	try:
		if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
			return False
		return bool(frappe.db.get_single_value(SETTINGS_DOCTYPE, "include_error_log", cache=True))
	except Exception:
		return False


def _source_settings() -> dict[str, dict]:
	"""Settings rows by source DocType; {} when the table does not exist (yet) or on any error."""
	try:
		if not frappe.db.table_exists(SOURCE_SETTING_DOCTYPE):
			return {}
		rows = frappe.get_all(
			SOURCE_SETTING_DOCTYPE,
			filters={"parent": SETTINGS_DOCTYPE, "parenttype": SETTINGS_DOCTYPE, "parentfield": "sources"},
			fields=["source_doctype", "enabled", "min_severity"],
			order_by="idx asc",
		)
	except Exception:
		return {}

	settings: dict[str, dict] = {}
	for row in rows:
		if row.source_doctype and row.source_doctype not in settings:
			min_severity = row.min_severity if row.min_severity in SEVERITIES else None
			settings[row.source_doctype] = {"enabled": bool(row.enabled), "min_severity": min_severity}
	return settings


def _apply_settings(config: SourceConfig, settings: dict | None) -> SourceConfig:
	config = replace(config, app_levels=config.mirror_levels)
	if not settings:
		return config
	levels = config.mirror_levels
	if settings.get("min_severity"):
		levels = effective_levels(settings["min_severity"])
	return replace(config, enabled=settings["enabled"], mirror_levels=levels)


def _parse_entry(entry: dict) -> SourceConfig:
	"""Validate one hook entry. Raises ValueError with the reason when invalid."""
	if not isinstance(entry, dict):
		raise ValueError("entry is not a dict")

	doctype = entry.get("doctype")
	if not doctype:
		raise ValueError("missing doctype")
	if doctype in OWN_DOCTYPES:
		raise ValueError(f"{doctype} cannot be registered as a source")
	if not frappe.db.exists("DocType", doctype):
		raise ValueError(f"DocType {doctype} does not exist")

	for key in ("source", "status_field", "message_field"):
		if not entry.get(key):
			raise ValueError(f"missing {key}")

	meta = frappe.get_meta(doctype)
	for key in ("status_field", "message_field"):
		fieldname = entry[key]
		if not (meta.has_field(fieldname) or fieldname in default_fields):
			raise ValueError(f"{key} {fieldname} not found on {doctype}")

	severity_map = dict(entry.get("severity_map") or {})
	for status, severity in severity_map.items():
		if severity not in SEVERITIES:
			raise ValueError(f"invalid severity {severity!r} for status {status!r}")

	raw_levels = entry.get("mirror_levels")
	mirror_levels = DEFAULT_MIRROR_LEVELS if raw_levels is None else frozenset(raw_levels)
	if not mirror_levels <= set(SEVERITIES):
		raise ValueError(f"invalid mirror_levels {sorted(mirror_levels)}")

	link_fields = entry.get("link_fields")
	if link_fields is not None:
		link_fields = tuple(link_fields)
		if len(link_fields) != 2:
			raise ValueError("link_fields must have two items")

	return SourceConfig(
		doctype=doctype,
		source=entry["source"],
		status_field=entry["status_field"],
		severity_map=severity_map,
		message_field=entry["message_field"],
		title_field=entry.get("title_field"),
		context_fields=tuple(entry.get("context_fields") or ()),
		group_fields=tuple(entry.get("group_fields") or ()),
		link_fields=link_fields,
		mirror_levels=mirror_levels,
		retry_method=entry.get("retry_method"),
		can_retry_method=entry.get("can_retry_method"),
		severity_method=entry.get("severity_method"),
	)


def _build_registry(errors: list[str]) -> dict[str, SourceConfig]:
	registry: dict[str, SourceConfig] = {}

	for entry in _load_hook_entries():
		try:
			config = _parse_entry(entry)
			if config.doctype in registry:
				raise ValueError(f"duplicate doctype {config.doctype}, first entry wins")
		except Exception as e:
			errors.append(f"{type(e).__name__}: {e}\n\n{entry!r}")
			continue
		registry[config.doctype] = config

	if "Error Log" not in registry and _include_error_log():
		registry["Error Log"] = ERROR_LOG_SOURCE

	settings = _source_settings()
	return {doctype: _apply_settings(config, settings.get(doctype)) for doctype, config in registry.items()}


def get_registry() -> dict[str, SourceConfig]:
	registry = getattr(frappe.local, LOCAL_ATTR, None)
	if registry is not None:
		return registry

	registry = frappe.cache.get_value(CACHE_KEY)
	if registry is not None:
		setattr(frappe.local, LOCAL_ATTR, registry)
		return registry

	if getattr(frappe.local, BUILDING_ATTR, False):
		# Re-entrant call (e.g. logging an error triggers a handler): do not rebuild
		return {}

	setattr(frappe.local, BUILDING_ATTR, True)
	try:
		errors: list[str] = []
		registry = _build_registry(errors)
		# Store first, so anything triggered by logging below sees a warm cache
		frappe.cache.set_value(CACHE_KEY, registry)
		setattr(frappe.local, LOCAL_ATTR, registry)
		for message in errors:
			try:
				frappe.log_error(title=INVALID_ENTRY_TITLE, message=message)
			except Exception:
				pass
	finally:
		setattr(frappe.local, BUILDING_ATTR, False)
	return registry


def get_source(doctype: str) -> SourceConfig | None:
	return get_registry().get(doctype)


def registered_doctypes() -> frozenset[str]:
	return frozenset(get_registry())


def clear_cache() -> None:
	setattr(frappe.local, LOCAL_ATTR, None)
	setattr(frappe.local, BUILDING_ATTR, False)
	frappe.cache.delete_value(CACHE_KEY)
