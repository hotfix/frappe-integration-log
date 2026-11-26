app_name = "integration_log"
app_title = "Integration Log"
app_publisher = "hotfix"
app_description = "Central log for integrations: collect, acknowledge, retry"
app_email = "1499166+hotfix@users.noreply.github.com"
app_license = "GPLv3"

required_apps = []

# Tile on the apps screen (default "Apps" desktop mode). Without this hook the app is installed
# but invisible on the home screen. The "Desktop Icons" mode uses desktop_icon/integration_log.json.
add_to_apps_screen = [
	{
		"name": "integration_log",
		"logo": "/assets/integration_log/images/logo.svg",
		"title": "Integration Log",
		"route": "/desk/integration-log",
		"has_permission": "integration_log.install.has_app_permission",
	}
]

# Registry cache must reflect hook changes after every migrate; backfill only on install
after_install = "integration_log.install.after_install"
after_migrate = "integration_log.install.after_migrate"

scheduler_events = {"daily": ["integration_log.tasks.cleanup_done"]}

# Mirror registered log DocTypes; the handler returns immediately for all others.
# on_change fires once per insert, save, submit, cancel and db_set.
doc_events = {
	"*": {
		"on_change": "integration_log.mirror.on_doc_event",
		"on_trash": "integration_log.mirror.on_doc_trash",
	}
}
