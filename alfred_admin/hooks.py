app_name = "alfred_admin"
app_title = "Alfred Admin"
app_publisher = "Venkatesh"
app_description = "Admin portal for Alfred - customer management, billing, usage monitoring"
app_email = "venkatesh@example.com"
app_license = "MIT"

required_apps = ["frappe"]

add_to_apps_screen = [
	{
		"name": "alfred_admin",
		"logo": "/assets/alfred_admin/images/alfred-admin-logo.svg",
		"title": "Alfred Admin",
		"route": "/app/alfred-customer",
	}
]

# Scheduled Tasks
scheduler_events = {
	"daily": [
		"alfred_admin.api.billing.check_trial_expirations",
	],
}
