"""Tests for Admin Portal APIs.

Run with: bench --site dev.alfred execute alfred_admin.test_admin_api.run_tests
"""

import json

import frappe
from frappe.utils import today, add_days


def run_tests():
	print("\n=== Alfred Admin API Tests ===\n")

	# Setup: Create a test plan
	print("Setup: Creating test plan...")
	if not frappe.db.exists("Alfred Plan", "Test Free"):
		plan = frappe.get_doc({
			"doctype": "Alfred Plan",
			"plan_name": "Test Free",
			"monthly_price": 0,
			"monthly_token_limit": 1000,
			"monthly_conversation_limit": 10,
			"max_users": 2,
			"is_active": 1,
		})
		plan.insert(ignore_permissions=True)
		frappe.db.commit()
	print("  Plan 'Test Free' ready\n")

	# Configure admin settings
	settings = frappe.get_single("Alfred Admin Settings")
	settings.default_plan = "Test Free"
	settings.trial_duration_days = 14
	settings.grace_period_days = 7
	settings.warning_threshold_percent = 80
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	from alfred_admin.api.usage import register_site, check_plan, report_usage

	# Test 1: Register a new site
	print("Test 1: Register new site...")
	# Mock the auth check by setting the service key
	settings.service_api_key = "test-service-key-123"
	settings.save(ignore_permissions=True)
	frappe.db.commit()

	# We need to bypass the auth check for testing
	test_site_id = "test-site-audit.example.com"

	# Clean up if exists
	if frappe.db.exists("Alfred Customer", test_site_id):
		frappe.db.sql("DELETE FROM `tabAlfred Subscription` WHERE customer = %s", test_site_id)
		frappe.db.sql("DELETE FROM `tabAlfred Usage Log` WHERE customer = %s", test_site_id)
		frappe.delete_doc("Alfred Customer", test_site_id, force=True)
		frappe.db.commit()

	# Direct function call (bypasses HTTP auth)
	# Mock the request header
	original_get_header = frappe.get_request_header
	frappe.get_request_header = lambda key, default="": "Bearer test-service-key-123" if key == "Authorization" else default
	try:
		result = register_site(test_site_id, site_url="https://test-site-audit.example.com", admin_email="admin@test.com")
		assert result["status"] == "created", f"Expected created, got {result}"
		assert frappe.db.exists("Alfred Customer", test_site_id)

		customer = frappe.get_doc("Alfred Customer", test_site_id)
		assert customer.current_plan == "Test Free"
		assert customer.status == "Active"
		print(f"  Created customer: {test_site_id}, plan: {customer.current_plan}")
		print("  PASSED\n")

		# Test 2: Register same site again (idempotent)
		print("Test 2: Re-register same site (idempotent)...")
		result = register_site(test_site_id, admin_email="new-admin@test.com")
		assert result["status"] == "updated"
		customer.reload()
		assert customer.admin_email == "new-admin@test.com"
		print(f"  Updated email: {customer.admin_email}")
		print("  PASSED\n")

		# Test 3: Check plan (within limits)
		print("Test 3: Check plan (within limits)...")
		result = check_plan(test_site_id)
		assert result["allowed"] is True
		assert result["tier"] == "Test Free"
		assert result["remaining_tokens"] == 1000
		print(f"  Allowed: {result['allowed']}, Remaining: {result['remaining_tokens']}")
		print("  PASSED\n")

		# Test 4: Report usage
		print("Test 4: Report usage...")
		result = report_usage(test_site_id, tokens=500, conversations=3, active_users=2)
		assert result["status"] == "ok"

		customer.reload()
		assert customer.total_tokens_used == 500
		assert customer.total_conversations == 3
		print(f"  Tokens: {customer.total_tokens_used}, Conversations: {customer.total_conversations}")
		print("  PASSED\n")

		# Test 5: Check plan after usage (should show remaining)
		print("Test 5: Check plan after usage...")
		result = check_plan(test_site_id)
		assert result["allowed"] is True
		assert result["remaining_tokens"] == 500
		print(f"  Remaining tokens: {result['remaining_tokens']}")
		print("  PASSED\n")

		# Test 6: Report usage to exceed limit
		print("Test 6: Exceed plan limit...")
		report_usage(test_site_id, tokens=600)
		result = check_plan(test_site_id)
		assert result["allowed"] is False
		assert "exceeded" in result["reason"].lower()
		print(f"  Allowed: {result['allowed']}, Reason: {result['reason']}")
		print("  PASSED\n")

		# Test 7: Admin override
		print("Test 7: Admin override bypasses limits...")
		customer.reload()
		customer.override_limits = 1
		customer.save(ignore_permissions=True)
		frappe.db.commit()

		result = check_plan(test_site_id)
		assert result["allowed"] is True
		assert result["tier"] == "override"
		print(f"  Tier: {result['tier']}")
		print("  PASSED\n")

		# Test 8: Check unknown site
		print("Test 8: Check unknown site...")
		result = check_plan("nonexistent-site.example.com")
		assert result["allowed"] is False
		print(f"  Allowed: {result['allowed']}, Reason: {result['reason']}")
		print("  PASSED\n")

		# Test 9: Subscription management
		print("Test 9: Subscription created with trial...")
		subs = frappe.get_all(
			"Alfred Subscription",
			filters={"customer": test_site_id},
			fields=["name", "status", "plan"],
		)
		assert len(subs) > 0, "Should have at least one subscription"
		assert subs[0]["status"] == "Trial"
		print(f"  Found {len(subs)} subscription(s), status: {subs[0]['status']}")
		print("  PASSED\n")

		# Test 10: Billing - subscribe to plan
		print("Test 10: Subscribe to plan...")
		from alfred_admin.api.billing import subscribe_to_plan
		result = subscribe_to_plan(test_site_id, "Test Free", "stripe_ref_123")
		assert result["status"] == "subscribed"
		print(f"  Subscribed: {result['subscription']}")
		print("  PASSED\n")

	finally:
		frappe.get_request_header = original_get_header

	# Cleanup
	print("Cleaning up...")
	frappe.db.sql("DELETE FROM `tabAlfred Subscription` WHERE customer = %s", test_site_id)
	frappe.db.sql("DELETE FROM `tabAlfred Usage Log` WHERE customer = %s", test_site_id)
	if frappe.db.exists("Alfred Customer", test_site_id):
		frappe.delete_doc("Alfred Customer", test_site_id, force=True)
	frappe.db.commit()
	print("Done.\n")

	print("=== All Admin API Tests PASSED ===\n")
