app_name = "integration_log"
app_title = "Integration Log"
app_publisher = "hotfix"
app_description = "Central log for integrations: collect, acknowledge, retry"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = []

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
