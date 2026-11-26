# Integration Log

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)
![Frappe v16](https://img.shields.io/badge/Frappe-v16-0089ff)
[![Release](https://img.shields.io/github/v/release/hotfix/frappe-integration-log)](https://github.com/hotfix/frappe-integration-log/releases)
[![CI](https://github.com/hotfix/frappe-integration-log/actions/workflows/ci.yml/badge.svg)](https://github.com/hotfix/frappe-integration-log/actions/workflows/ci.yml)

A central log for integrations on Frappe v16. It is meant for teams that run several connectors (shops,
marketplaces, carriers, payment providers) and do not want to check a separate log in every app. Apps that
write their own sync or error log DocType register it with one hook entry. Integration Log mirrors the
interesting rows into a single `Integration Log Entry` list, where users acknowledge, reopen and (if the
source supports it) retry them. A workspace shows open errors and warnings at a glance.

There is no dependency on ERPNext, and consumer apps do not need to depend on Integration Log. It is used by
[modified_connector](https://github.com/hotfix/frappe-modified-connector), the ERPNext connector for
modified eCommerce shops, which registers its sync log with it.

## Features

- One hook entry (`integration_logs`) per log DocType; no code dependency for the consumer app.
- Mirroring on every change of the source row, with severities, automatic resolution per group and
  *Area* per entry.
- Acknowledge (with note), reopen and retry, from the form and the list.
- Optional mirroring of Frappe's Error Log as the source "System".
- Per-source settings: enable or disable a source and set its minimum severity.
- Backfill of existing rows, daily retention cleanup.
- Workspace with number cards, a chart and a report; German translation.

## Requirements

- Frappe v16. ERPNext is not required.

## Install

```bash
bench get-app https://github.com/hotfix/frappe-integration-log
bench --site your.site install-app integration_log
bench build --app integration_log
```

Installing creates the role `Integration Log Manager` and queues a backfill of existing rows of all
registered sources. `System Manager` and `Integration Log Manager` can work with the entries. Only
`System Manager` can change **Integration Log Settings**.

## Versions and updates

Development happens on `main`. Releases are git tags `vX.Y.Z` with a GitHub Release. Production
installs should pin a tag:

```bash
bench get-app https://github.com/hotfix/frappe-integration-log --branch v0.1.0
```

In a Docker image, use the tag as the branch of the app in `apps.json`. Changes per version are
listed in [CHANGELOG.md](CHANGELOG.md).

## Quick start

1. Install a consumer app that registers its log, for example
   [modified_connector](https://github.com/hotfix/frappe-modified-connector), or add an `integration_logs`
   entry to your own app (see below) and run `bench --site your.site migrate`.
2. Open the tile *Integration Log* on the home screen. Errors and warnings of the registered sources
   appear in the workspace and in the *Integration Log Entry* list.
3. Optional: in **Integration Log Settings**, tick *Include Error Log*, adjust the *Sources* table and
   click *Backfill now* to mirror rows that existed before.
4. Work through the open entries: fix the cause, then **Retry** (if the source supports it) or
   **Acknowledge** them.

## Screenshots

Screenshots will be added to [`docs/images/`](docs/images/).

## The interface: `integration_logs` hook

A consumer app declares its log DocType in its own `hooks.py`:

```python
integration_logs = [
	{
		"doctype": "My Sync Log",
		"source": "My App",
		"status_field": "status",
		"severity_map": {"Failed": "error", "Skipped": "warning", "Done": "info"},
		"message_field": "error_message",
		"title_field": "reference",
		"area_field": "direction",
		"context_fields": ["direction", "reference"],
		"group_fields": ["direction", "reference"],
		"link_fields": ["reference_doctype", "reference_name"],
		"mirror_levels": ["error", "warning"],
		"retry_method": "my_app.sync.retry_log",
		"can_retry_method": "my_app.sync.can_retry_log",
	}
]
```

### Required keys

| Key | Type | Meaning |
| --- | --- | --- |
| `doctype` | str | The log DocType. It must exist. |
| `source` | str | Name shown to users and used for grouping in the chart and report. |
| `status_field` | str | Field with the status of the row. It must exist on the DocType (standard fields like `modified` also count). |
| `message_field` | str | Field with the message text. It must exist on the DocType. |

### Optional keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `severity_map` | dict | `{}` | Maps a status value to `error`, `warning` or `info`. |
| `severity_method` | str (dotted path) | none | Function `f(doc)` returning `"error"`, `"warning"` or `"info"`. |
| `area_field` | str | none | Field with the area (part of the system) of the row, shown as *Area* on the entry. It must exist on the DocType. Values are cut at 140 characters. |
| `title_field` | str | none | Field used as title. Without it, the first line of the message, then the document name. Titles are cut at 140 characters. |
| `context_fields` | list of str | `[]` | Fields stored as JSON in the entry's `context`. |
| `group_fields` | list of str | `[]` | Fields that identify "the same thing" for automatic resolution (see below). |
| `link_fields` | list of two str | none | `[doctype_field, name_field]` of the source row; fills the entry's linked document. |
| `mirror_levels` | list of str | `["error", "warning"]` | Severities that create an entry. |
| `retry_method` | str (dotted path) | none | Enables the Retry button. |
| `can_retry_method` | str (dotted path) | none | Decides per row whether Retry is offered. |

Entries that fail validation (missing or unknown field, unknown severity, bad `mirror_levels`,
`link_fields` without two items, DocType not installed, `Integration Log Entry` or
`Integration Log Settings` itself) are skipped and reported once as an Error Log
titled "Integration Log: invalid registry entry". If a DocType is registered twice, the first entry
wins. The registry is cached and rebuilt on every `bench migrate`.

### Severity rules

1. With `severity_method`, its result is used. An unknown return value counts as `warning`.
2. Otherwise `severity_map[status]` is used. A status missing from the map counts as `warning`.
3. The built-in Error Log source has no status and is always `error`.

Only rows whose severity is in `mirror_levels` produce an entry. Everything else (for example a
successful run) is not mirrored. `info` is informational: when it is mirrored, the entry is created
as `Resolved` (never `Open`), see below.

### Mirroring, groups and states

An entry has the states `Open`, `Acknowledged` and `Resolved`. Mirroring runs on every change of the
source row (insert, save, submit, cancel and `db_set`, through the `on_change` document event),
inside the same transaction. A failure while mirroring is rolled back to a savepoint and reported in
the Error Log; it does not make the source save fail. The one exception: if the transaction itself is
lost (a database deadlock, or the rollback to the savepoint fails), the original exception is raised
to the caller, because committing the rest of the transaction would silently lose earlier writes.

- A new row with a mirrored severity creates an `Open` entry. Later updates of the row refresh the
  entry (severity, message, status, last seen).
- If the row changes to a severity that is not mirrored, an `Open` entry becomes `Resolved`
  automatically.
- **Info entries are history.** A row with severity `info` that is mirrored (`info` in the mirror
  levels) gets an entry that is `Resolved` automatically from the start; an `Open` entry of the row
  is resolved, an `Acknowledged` one stays acknowledged. The row still resolves its group like a
  non-mirrored row. If the row later changes to `warning` or `error`, the entry opens again.
- **Group resolution.** With `group_fields`, the values of those fields are hashed into a group key.
  When a row of the group is saved with a non-mirrored severity or with `info` (for example a later
  successful run), all `Open` entries of the same group that are not newer than that row are resolved
  automatically.
  If all group fields are empty, no group is formed.
- **Acknowledged stays acknowledged.** An acknowledged entry is never reopened or resolved by the
  mirror, even if the source row changes again. Only the displayed data is refreshed. A user can reopen
  it manually.
- An automatically resolved entry opens again when its own source row changes again (its `modified`
  is newer than the entry's *last seen*). Re-applying an unchanged row, for example by a backfill or
  `sync`, does not reopen it.
- Deleting the source row deletes its mirror.

### Writers that bypass document events

`frappe.db.set_value` and raw SQL do not fire document events (`doc.db_set` does). After such a
write, call `sync`, guarded so the consumer keeps working without Integration Log:

```python
import frappe

if "integration_log" in frappe.get_installed_apps():
	frappe.get_attr("integration_log.mirror.sync")("My Sync Log", name)
```

`sync` re-reads the row and mirrors it (or removes the mirror when the row is gone). It returns the
entry name or `None`, and like the document events it only raises when the transaction is lost.

### Retry contract

```python
def retry_log(source_name: str) -> None:
	"""Re-run the sync for this log row. Raise an exception on failure."""


def can_retry_log(source_name: str) -> bool:
	"""True if a retry makes sense for this row."""
```

- `retry_method(source_name)` receives the name of the source row. The consumer checks its own
  permissions and may commit. If it raises, Integration Log rolls back the writes of the request that
  are not committed yet and shows the exception text. On success the mirror is synced again and the
  user sees the generic message "Retry triggered."; details about what the retry did come from the
  consumer's own messages (for example `frappe.msgprint`), which are shown as well.
- `can_retry_method(source_name) -> bool` decides whether the button is shown. Without it, Retry is
  always offered for sources with a `retry_method`. An exception counts as "not available".
- A consumer must not import `integration_log`. Guard every use with the installed apps and resolve
  the function by its dotted path:

```python
import frappe

if "integration_log" in frappe.get_installed_apps():
	frappe.get_attr("integration_log.mirror.sync")("My Sync Log", name)
```

### A full example entry

With the registration above, a log row that failed:

```python
frappe.get_doc(
	{
		"doctype": "My Sync Log",
		"status": "Failed",
		"direction": "Export",
		"reference": "SINV-0042",
		"reference_doctype": "Sales Invoice",
		"reference_name": "SINV-0042",
		"error_message": "Shop API returned 503\nService unavailable",
	}
).insert()
```

creates this `Integration Log Entry`:

| Field | Value |
| --- | --- |
| `state` | Open |
| `severity` | error |
| `source` | My App |
| `title` | SINV-0042 |
| `message` | The full error message (both lines) |
| `source_status` | Failed |
| `source_doctype` / `source_name` | My Sync Log / the row's name |
| `group_key` | sha1 of `["Export", "SINV-0042"]` |
| `context` | `{"direction": "Export", "reference": "SINV-0042"}` |
| `ref_doctype` / `ref_name` | Sales Invoice / SINV-0042 |
| `occurred_on` / `last_seen` | creation and modified time of the row |

If a later run for the same direction and reference is saved with status `Done`, this entry becomes
`Resolved` automatically.

## Error Log option

In **Integration Log Settings**, tick *Include Error Log* to mirror Frappe's Error Log as a built-in
source named "System" (severity always `error`, title from the method, linked document from
`reference_doctype` / `reference_name`). Error Logs are not mirrored while the option is off.

## Source settings

The table *Sources* in **Integration Log Settings** has one row per registered source (each hook
entry, plus "System" while *Include Error Log* is ticked). Rows for new sources are added when the
settings form is opened; save the form to keep them.

- *Enabled*: untick to stop mirroring the source. No new entries are created, existing entries are
  neither updated nor resolved, and the backfill skips the source. Deleting a source row still
  deletes its mirror, and acknowledge, reopen and retry keep working on existing entries (after a
  retry the entry itself is not updated while the source is disabled).
- *Minimum Severity*: mirror every severity from the chosen one upwards (`info` = all, `warning` =
  warning and error, `error` = errors only). Empty uses the app's `mirror_levels`, shown in
  *App Default*. A row that falls below the level resolves its open entry on its next change. With
  `info`, info entries are kept as resolved history and removed by retention; they never stay
  `Open`, and a later successful row still resolves earlier errors of its group.

A source without a row (for example before the settings were ever opened) keeps the hook values.
Rows of sources that are no longer registered are ignored and can be deleted. Saving the settings
removes rows whose DocType no longer exists (for example after uninstalling the consumer app), blank
rows and duplicate rows (the first row per DocType is kept). Changes apply as soon as the settings
are saved. Disabled sources are skipped by *Backfill now*; when no enabled source is left, the
button says so.

## Retention and cleanup

A daily job deletes `Acknowledged` and `Resolved` entries that were last modified more than
*Retention (days)* ago (default 90, `0` = never delete). It also deletes mirrors whose source row no
longer exists, whatever their state (Frappe's own log clearing deletes rows without a trash event),
and all mirrors of a source DocType that no longer exists (for example after uninstalling the
consumer app).
`Open` entries are never removed because of age.

## Backfill

Rows that existed before the app was installed, or before a source was registered, are mirrored by a
background job. It is queued on install and can be started again with the button *Backfill now* in
**Integration Log Settings** (or `integration_log.api.start_backfill`); both need write permission
on the settings, so only `System Manager` can start it. Running it more than once is safe. After adding a
source, run `bench --site your.site migrate` so the registry is rebuilt, then start the backfill.

**Upgrading to a version with *Area*:** after updating, run `bench --site your.site migrate`, then
*Backfill now* in **Integration Log Settings**. The backfill writes *Area* into the existing entries
of every enabled source that sets `area_field`. Entries of disabled sources keep their old values
until the source is enabled again (and backfilled). Writing *Area* saves the entry, so resolved and
acknowledged entries start their retention period again once.

## Permissions

The mirror copies message texts of the source rows into `Integration Log Entry` without checking the
permissions on the source DocType. Everyone with access to the entries (`Integration Log Manager`,
`System Manager`) sees these texts. Do not register logs whose messages contain data that these roles
may not see. Acknowledge, reopen and retry need write permission on the entry; backfill needs write
permission on the settings. **Integration Log Settings** can be read by `Integration Log Manager` but
only changed by `System Manager`, because the *Include Error Log* option would expose Error Log
tracebacks. The state-changing API methods (`acknowledge`, `reopen`, `retry`, `start_backfill`)
accept POST only.

## Workspace

The workspace *Integration Log* (German: *Integrations-Protokoll*) has the number cards *Open Errors*
and *Open Warnings*, the chart *Open by Source*, and shortcuts to the open entries, the settings and
the report *Integration Log by Source*. The app ships a standard `Sidebar` for the module, so the
module shows up in the Frappe v16 desk navigation with links to the workspace, the entries, the
report and the settings.

On the home screen the app appears as the tile *Integration Log* (hook `add_to_apps_screen`, shown to
System Manager and Integration Log Manager). Sites that use the "Desktop Icons" desktop mode get the
shipped Desktop Icon `Integration Log` instead (`desktop_icon/integration_log.json`). Both open the
workspace. The tile logo is served from `assets/integration_log`, which `bench build` creates for apps
present in the bench (a plain restart does not recreate the symlink). After installing or updating run
`bench --site <site> migrate` and `bench build`.

## Translations

German translations are in `integration_log/locale/de.po`. Update the template with
`bench generate-pot-file --app integration_log` and compile with
`bench compile-po-to-mo --app integration_log` (also done by `bench build`).

## Feedback and contributing

Reports from real setups make this app better, so please get in touch:

- **Found a bug?** [Open an issue](https://github.com/hotfix/frappe-integration-log/issues/new/choose) with
  the steps to reproduce it and your versions of Frappe, ERPNext (if installed) and this app.
- **Missing a feature or have an idea?** Open a feature request on the same page. Questions are welcome too.
- **Want to fix something yourself?** Pull requests are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).
- **Security issue?** Please do not open a public issue; see [SECURITY.md](SECURITY.md).

The app is maintained in spare time and provided as is, so there are no guaranteed response times, but every
report is read.

If the app is useful to you, a ⭐ on GitHub helps others find it.

## License

GPLv3, see [LICENSE](LICENSE).
