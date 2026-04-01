"""Billing and payment integration for Alfred Admin.

Handles trial lifecycle, subscription management, and payment webhook processing.
Uses Frappe Payments for Stripe/Razorpay integration.
"""

import frappe
from frappe import _
from frappe.utils import today, getdate, add_days, date_diff


def check_trial_expirations():
	"""Daily scheduler job: check and handle expired trials.

	- Sends warning 3 days before trial ends
	- Cancels expired trials and suspends customer
	"""
	settings = frappe.get_single("Alfred Admin Settings")
	grace_days = settings.grace_period_days or 7

	# Find expiring trials (3 days warning)
	warning_date = add_days(today(), 3)
	expiring_trials = frappe.get_all(
		"Alfred Subscription",
		filters={
			"status": "Trial",
			"end_date": warning_date,
		},
		fields=["name", "customer", "end_date"],
	)

	for trial in expiring_trials:
		try:
			customer = frappe.get_doc("Alfred Customer", trial.customer)
			frappe.sendmail(
				recipients=[customer.admin_email],
				subject="Alfred Trial Expiring Soon",
				message=f"""
					<p>Your Alfred trial for {customer.site_id} expires on {trial.end_date}.</p>
					<p>Please subscribe to a plan to continue using Alfred.</p>
				""",
				now=True,
			)
		except Exception:
			pass

	# Cancel expired trials
	expired_trials = frappe.get_all(
		"Alfred Subscription",
		filters={
			"status": "Trial",
			"end_date": ["<", today()],
		},
		fields=["name", "customer"],
	)

	for trial in expired_trials:
		try:
			sub = frappe.get_doc("Alfred Subscription", trial.name)
			sub.status = "Expired"
			sub.save(ignore_permissions=True)

			# Suspend customer after grace period
			customer = frappe.get_doc("Alfred Customer", trial.customer)
			trial_end = getdate(sub.end_date)
			if date_diff(today(), trial_end) > grace_days:
				customer.status = "Suspended"
				customer.save(ignore_permissions=True)
		except Exception as e:
			frappe.log_error(f"Trial expiration error for {trial.customer}: {e}")

	frappe.db.commit()


@frappe.whitelist()
def subscribe_to_plan(customer_name, plan_name, payment_reference=""):
	"""Create a new subscription for a customer."""
	customer = frappe.get_doc("Alfred Customer", customer_name)
	plan = frappe.get_doc("Alfred Plan", plan_name)

	# Cancel existing active subscription
	existing = frappe.get_all(
		"Alfred Subscription",
		filters={"customer": customer_name, "status": ["in", ["Active", "Trial"]]},
		pluck="name",
	)
	for sub_name in existing:
		sub = frappe.get_doc("Alfred Subscription", sub_name)
		sub.status = "Cancelled"
		sub.save(ignore_permissions=True)

	# Create new subscription
	new_sub = frappe.get_doc({
		"doctype": "Alfred Subscription",
		"customer": customer_name,
		"plan": plan_name,
		"status": "Active",
		"start_date": today(),
		"payment_reference": payment_reference,
	})
	new_sub.insert(ignore_permissions=True)

	# Update customer
	customer.current_plan = plan_name
	customer.status = "Active"
	customer.save(ignore_permissions=True)
	frappe.db.commit()

	return {"status": "subscribed", "subscription": new_sub.name, "plan": plan_name}


@frappe.whitelist()
def cancel_subscription(customer_name):
	"""Cancel a customer's active subscription."""
	settings = frappe.get_single("Alfred Admin Settings")
	grace_days = settings.grace_period_days or 7

	active_subs = frappe.get_all(
		"Alfred Subscription",
		filters={"customer": customer_name, "status": "Active"},
		pluck="name",
	)

	for sub_name in active_subs:
		sub = frappe.get_doc("Alfred Subscription", sub_name)
		sub.status = "Cancelled"
		sub.end_date = add_days(today(), grace_days)
		sub.save(ignore_permissions=True)

	frappe.db.commit()
	return {"status": "cancelled", "grace_period_ends": str(add_days(today(), grace_days))}
