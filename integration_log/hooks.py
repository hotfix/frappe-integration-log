app_name = "integration_log"
app_title = "Integration Log"
app_publisher = "hotfix"
app_description = "Central log for integrations: collect, acknowledge, retry"
app_email = "info@hotfix.example"
app_license = "GPLv3"

required_apps = []

# Registry cache must reflect hook changes after every migrate
after_install = after_migrate = "integration_log.install.after_install"
