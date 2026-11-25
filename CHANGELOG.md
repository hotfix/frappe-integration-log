# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - unreleased

### Added

- Source registry: apps register their log DocType through the `integration_logs` hook in `hooks.py`.
  Entries are validated, invalid ones (including Integration Log's own DocTypes) are skipped and
  reported in the Error Log.
- Mirroring of registered log DocTypes into `Integration Log Entry` on every change of the source row
  (`on_change`: insert, save, submit, cancel and `db_set`), inside the source transaction. Mirror
  failures are rolled back to a savepoint and reported without failing the source save; only a lost
  transaction (deadlock, failed savepoint rollback) re-raises the original exception. A registry
  lookup error never reaches the document save. Severity via `severity_map` or `severity_method`,
  `mirror_levels`, `context_fields`, `link_fields` and `title_field`.
- Grouping with `group_fields`: a successful run resolves older open entries of the same group.
  Acknowledged entries stay acknowledged. An automatically resolved entry only reopens when its own
  source row actually changes, not when an unchanged row is re-applied (backfill, `sync`).
- `integration_log.mirror.sync(doctype, name)` for writers that use `frappe.db.set_value`.
- Optional built-in "System" source for the Error Log (setting `Include Error Log`). Integration Log
  Settings can only be changed by `System Manager`.
- Actions on entries: acknowledge (with note), reopen and retry, as POST-only API methods plus form and
  list buttons. Retry calls the source's `retry_method`; `can_retry_method` controls availability.
- Backfill of existing source rows (queued after install, "Backfill now" in the settings, System
  Manager only).
- Daily cleanup: retention for acknowledged and resolved entries (default 90 days), removal of
  mirrors whose source row no longer exists and of all mirrors of a source DocType that no longer
  exists.
- Role `Integration Log Manager`.
- Workspace "Integration Log" with number cards "Open Errors" and "Open Warnings", the chart
  "Open by Source" and the report "Integration Log by Source", plus a standard `Sidebar` for the v16
  desk navigation.
- German translation.
