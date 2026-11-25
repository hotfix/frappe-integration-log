app_name = "integration_log"
app_title = "Integration Log"
app_publisher = "hotfix"
app_description = "Central log for integrations: collect, acknowledge, retry"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = []

# Registry cache must reflect hook changes after every migrate
after_install = after_migrate = "integration_log.install.after_install"

# Mirror registered log DocTypes; the handler returns immediately for all others
doc_events = {
	"*": {
		"after_insert": "integration_log.mirror.on_doc_event",
		"on_update": "integration_log.mirror.on_doc_event",
		"on_trash": "integration_log.mirror.on_doc_trash",
	}
}
