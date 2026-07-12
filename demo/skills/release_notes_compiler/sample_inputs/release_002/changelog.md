# Internal changelog — release_002

## Bug fixes
- Webhooks no longer duplicate on retry. The idempotency key is now honored end-to-end. Customers should no longer see double-delivered events on transient failures.
- Audit log CSV export now respects the workspace timezone setting; prior export silently used UTC.
- Concurrent role edits by two admins on the same role now serialize correctly; previously the second write could overwrite the first without warning.

## New features
- Billing reports can be exported as CSV from the workspace settings page.
- Custom retention policies — configure per workspace, with a 90-day default. Required for several compliance use cases.
- Webhook delivery failures now surface in the activity log with retry status and last error.

## Docs
- Documented the idempotency-key contract for webhook subscribers.
