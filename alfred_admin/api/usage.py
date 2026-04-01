"""Usage reporting and plan enforcement API endpoints.

Called by the Processing App to:
1. Report usage after conversations complete
2. Check plan limits before processing tasks
3. Register new customer sites

Authentication: service API key in Authorization header,
validated against Alfred Admin Settings.service_api_key.
"""

import json

import frappe
from frappe import _
from frappe.utils import today, getdate, add_days, nowdate


def _validate_service_key():
	"""Validate the service API key from the Authorization header."""
	auth = frappe.get_request_header("Authorization", "")
	if auth.startswith("Bearer "):
		key = auth[7:]
	else:
		key = auth

	if not key:
		frappe.throw(_("Missing service API key"), frappe.AuthenticationError)

	settings = frappe.get_single("Alfred Admin Settings")
	expected = settings.get_password("service_api_key")

	if not expected or key != expected:
		frappe.throw(_("Invalid service API key"), frappe.AuthenticationError)


@frappe.whitelist(allow_guest=True)
def report_usage(site_id, tokens=0, conversations=0, active_users=0, date=None):
	"""Report usage from a customer site.

	Creates or updates an Alfred Usage Log for the given date.
	Also updates cumulative totals on the Alfred Customer record.
	"""
	_validate_service_key()

	if not frappe.db.exists("Alfred Customer", site_id):
		frappe.throw(_("Unknown site: {0}").format(site_id), frappe.DoesNotExistError)

	usage_date = date or today()
	tokens = int(tokens or 0)
	conversations = int(conversations or 0)
	active_users = int(active_users or 0)

	# Upsert usage log for the date
	existing = frappe.get_all(
		"Alfred Usage Log",
		filters={"customer": site_id, "date": usage_date},
		limit=1,
	)

	if existing:
		log = frappe.get_doc("Alfred Usage Log", existing[0].name)
		log.tokens_used = (log.tokens_used or 0) + tokens
		log.conversations = (log.conversations or 0) + conversations
		log.active_users = max(log.active_users or 0, active_users)
		log.save(ignore_permissions=True)
	else:
		frappe.get_doc({
			"doctype": "Alfred Usage Log",
			"customer": site_id,
			"date": usage_date,
			"tokens_used": tokens,
			"conversations": conversations,
			"active_users": active_users,
		}).insert(ignore_permissions=True)

	# Update customer totals
	customer = frappe.get_doc("Alfred Customer", site_id)
	customer.total_tokens_used = (customer.total_tokens_used or 0) + tokens
	customer.total_conversations = (customer.total_conversations or 0) + conversations
	customer.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "ok", "site_id": site_id, "date": usage_date}


@frappe.whitelist(allow_guest=True)
def check_plan(site_id):
	"""Check if a site is within its plan limits.

	Returns:
		allowed (bool), remaining_tokens, tier, reason
	"""
	_validate_service_key()

	if not frappe.db.exists("Alfred Customer", site_id):
		return {
			"allowed": False,
			"remaining_tokens": 0,
			"tier": "unknown",
			"reason": f"Unknown site: {site_id}",
		}

	customer = frappe.get_doc("Alfred Customer", site_id)

	# Admin override
	if customer.override_limits:
		if customer.override_expiry and getdate(customer.override_expiry) < getdate(today()):
			pass  # Override expired, proceed with normal check
		else:
			return {
				"allowed": True,
				"remaining_tokens": -1,
				"tier": "override",
				"reason": "Admin override active",
			}

	# Check customer status
	if customer.status != "Active":
		return {
			"allowed": False,
			"remaining_tokens": 0,
			"tier": customer.current_plan or "none",
			"reason": f"Customer status: {customer.status}",
		}

	# Check plan limits
	if not customer.current_plan:
		return {
			"allowed": False,
			"remaining_tokens": 0,
			"tier": "none",
			"reason": "No plan assigned",
		}

	plan = frappe.get_doc("Alfred Plan", customer.current_plan)

	# Get current month usage
	from frappe.utils import get_first_day, get_last_day
	first_day = get_first_day(today())
	last_day = get_last_day(today())

	monthly_usage = frappe.db.sql("""
		SELECT COALESCE(SUM(tokens_used), 0) as tokens, COALESCE(SUM(conversations), 0) as convs
		FROM `tabAlfred Usage Log`
		WHERE customer = %s AND date BETWEEN %s AND %s
	""", (site_id, first_day, last_day), as_dict=True)[0]

	tokens_used = monthly_usage.tokens or 0
	convs_used = monthly_usage.convs or 0

	remaining_tokens = max(0, (plan.monthly_token_limit or 0) - tokens_used)
	remaining_convs = max(0, (plan.monthly_conversation_limit or 0) - convs_used)

	# Check warning threshold
	settings = frappe.get_single("Alfred Admin Settings")
	threshold = (settings.warning_threshold_percent or 80) / 100.0
	token_usage_pct = tokens_used / max(plan.monthly_token_limit or 1, 1)

	if remaining_tokens <= 0 or remaining_convs <= 0:
		return {
			"allowed": False,
			"remaining_tokens": remaining_tokens,
			"tier": plan.plan_name,
			"reason": "Monthly limit exceeded",
		}

	warning = None
	if token_usage_pct >= threshold:
		warning = f"Token usage at {int(token_usage_pct * 100)}% of monthly limit"

	return {
		"allowed": True,
		"remaining_tokens": remaining_tokens,
		"remaining_conversations": remaining_convs,
		"tier": plan.plan_name,
		"reason": None,
		"warning": warning,
	}


@frappe.whitelist(allow_guest=True)
def register_site(site_id, site_url="", admin_email=""):
	"""Register a new customer site (idempotent).

	If the site already exists, updates the URL and email.
	"""
	_validate_service_key()

	if frappe.db.exists("Alfred Customer", site_id):
		customer = frappe.get_doc("Alfred Customer", site_id)
		if site_url:
			customer.site_url = site_url
		if admin_email:
			customer.admin_email = admin_email
		customer.save(ignore_permissions=True)
		frappe.db.commit()
		return {"status": "updated", "site_id": site_id}

	# Assign default plan
	settings = frappe.get_single("Alfred Admin Settings")
	default_plan = settings.default_plan

	customer = frappe.get_doc({
		"doctype": "Alfred Customer",
		"site_id": site_id,
		"site_url": site_url,
		"admin_email": admin_email or "admin@" + site_id,
		"current_plan": default_plan,
		"status": "Active",
		"trial_start": today(),
		"trial_end": add_days(today(), settings.trial_duration_days or 14),
	})
	customer.insert(ignore_permissions=True)

	# Create trial subscription
	if default_plan:
		frappe.get_doc({
			"doctype": "Alfred Subscription",
			"customer": site_id,
			"plan": default_plan,
			"status": "Trial",
			"start_date": today(),
			"end_date": add_days(today(), settings.trial_duration_days or 14),
		}).insert(ignore_permissions=True)

	frappe.db.commit()
	return {"status": "created", "site_id": site_id, "plan": default_plan}
