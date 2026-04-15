# Alfred Admin

Admin portal for Alfred - customer management, subscription plans, usage
tracking, and trial lifecycle. Installed alongside Frappe on a dedicated
admin site. The processing app talks to it via a service API key to check
quotas and resolve the tier-locked pipeline mode for each customer.

## What lives here

- **Alfred Customer** - one record per customer site. Tracks `site_id`,
  current plan, status (`Active` / `Suspended` / `Cancelled`), admin override
  flags, and cumulative token / conversation counts.
- **Alfred Plan** - subscription tier definition: plan name, monthly price,
  token limit, conversation limit, max users, features, and
  **`pipeline_mode`** (`full` or `lite`) - this is the tier-locked crew mode
  that `check_plan` returns to the processing app. Lower tiers can be locked
  to `lite` (single-agent fast pass) while higher tiers unlock `full`
  (6-agent SDLC). Defaults to `full` for new plans.
- **Alfred Subscription** - billing lifecycle: `Trial` → `Active` → `Past
  Due` → `Cancelled / Expired`. A daily scheduled job
  (`check_trial_expirations`) warns 3 days before a trial ends and suspends
  customers after the grace period.
- **Alfred Usage Log** - daily per-customer aggregate of `tokens_used`,
  `conversations`, `active_users`. The processing app posts one row per day
  via `report_usage` after each pipeline run.
- **Alfred Admin Settings** - singleton with `service_api_key`,
  `grace_period_days`, `default_plan`, `warning_threshold_percent`,
  `trial_duration_days`.

## API Endpoints (service-key authenticated)

Called by the processing app with `Authorization: Bearer <service_api_key>`:

| Endpoint | Purpose |
|---|---|
| `alfred_admin.api.usage.report_usage` | Daily token / conversation / active-user rollup from a customer site |
| `alfred_admin.api.usage.check_plan` | Can this site run? Remaining tokens, warning threshold, tier-locked `pipeline_mode` |
| `alfred_admin.api.usage.register_site` | First-time registration, creates a trial subscription |

The processing app's pipeline calls `check_plan` at the start of every
conversation (`_phase_plan_check`). The response shape:

```json
{
  "allowed": true,
  "remaining_tokens": 85000,
  "remaining_conversations": 40,
  "tier": "Pro",
  "warning": null,
  "pipeline_mode": "full"
}
```

## Billing Endpoints (System-Manager gated)

| Endpoint | Purpose |
|---|---|
| `alfred_admin.api.billing.subscribe_to_plan` | Create a new subscription (cancels existing) |
| `alfred_admin.api.billing.cancel_subscription` | Mark active subscription cancelled with grace period |

Both endpoints call `_require_billing_admin()` which throws if the caller
isn't a System Manager. This matters because the endpoints use
`ignore_permissions=True` internally for customer/subscription writes - the
role check is the only thing stopping an arbitrary logged-in portal user
from mutating any customer's plan.

## Installation

```bash
bench get-app alfred_admin
bench --site your-admin-site install-app alfred_admin
bench --site your-admin-site migrate
```

Then configure:

1. Open `/app/alfred-admin-settings`
2. Set **Service API Key** - long random string, also set in the processing
   app's `.env` as `ADMIN_SERVICE_KEY`
3. Set **Default Plan** - auto-assigned to new customers
4. Create plans at `/app/alfred-plan`, setting the **Pipeline Mode** field
   per tier (`full` for Pro+, `lite` for starter/free)

## Scheduled Jobs

- `check_trial_expirations` runs daily. Sends a warning email 3 days before
  a trial ends, suspends customers whose grace period has elapsed. Tolerates
  a missing `Alfred Admin Settings` singleton on fresh installs (falls back
  to a 7-day grace period default).

## License

MIT
