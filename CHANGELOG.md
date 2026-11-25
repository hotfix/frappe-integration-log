# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - unreleased

### Added

- Source registry: apps register their log DocType through the `integration_logs` hook in `hooks.py`.
  Entries are validated, invalid ones are skipped and reported in the Error Log.
- Mirroring of registered log DocTypes into `Integration Log Entry`, inside the source transaction and
  without ever making the source save fail. Severity via `severity_map` or `severity_method`,
  `mirror_levels`, `context_fields`, `link_fields` and `title_field`.
- Grouping with `group_fields`: a successful run resolves older open entries of the same group.
  Acknowledged entries stay acknowledged.
- `integration_log.mirror.sync(doctype, name)` for writers that use `frappe.db.set_value`.
- Optional built-in "System" source for the Error Log (setting `Include Error Log`).
- Actions on entries: acknowledge (with note), reopen and retry, as POST-only API methods plus form and
  list buttons. Retry calls the source's `retry_method`; `can_retry_method` controls availability.
- Backfill of existing source rows (queued after install, "Backfill now" in the settings).
- Daily cleanup: retention for acknowledged and resolved entries (default 90 days) and removal of
  mirrors whose source row no longer exists.
- Role `Integration Log Manager`.
- Workspace "Integration Log" with number cards "Open Errors" and "Open Warnings", the chart
  "Open by Source" and the report "Integration Log by Source".
- German translation.
