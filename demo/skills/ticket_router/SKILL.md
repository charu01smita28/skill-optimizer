---
name: ticket-router
description: Route an enterprise support ticket to the responsible team with priority, category, SLA, and reason codes. Use when given customer ticket or internal request text.
primary_fields: ["team", "priority", "category"]
---

# Ticket Router

Given customer or internal ticket text, classify the responsible team, urgency, and category. Output canonical JSON the downstream workflow can act on.

## Teams

- **billing** — invoices, payments, renewals, credits, refunds, subscription changes
- **legal** — contracts, DPAs, NDAs, redlines, indemnity, compliance questions
- **support** — account access, login issues, configuration help, how-to questions
- **engineering** — bugs, error reports, broken integrations, performance regressions
- **security** — suspicious activity, breaches, credential exposure, vulnerability reports
- **sales** — pricing, upgrades, expansion, prospect questions, churn risk
- **hr** — internal personnel matters, onboarding, benefits

## Priorities

- **urgent** — production-impacting, security incident, imminent renewal at risk
- **high** — payment failure, access blocking work, contract deadline within 48h
- **medium** — non-blocking issue, planning question, feature ask with timeline
- **low** — informational, no clear deadline, future improvement

## Categories

- `payment_failure`
- `account_access`
- `bug_report`
- `contract_question`
- `data_request`
- `security_incident`
- `renewal_risk`
- `feature_request`
- `general_inquiry`

## Process

1. Read the ticket text in the input file.
2. Identify the responsible team from the language and intent.
3. Assign priority based on impact and time sensitivity.
4. Pick the most specific category.
5. Set `sla_hours` based on priority (urgent=2, high=4, medium=24, low=72).
6. Set `requires_human_review` to `true` when the ticket spans multiple teams, mentions legal/compliance risk, or you are unsure.
7. List 1–4 short `reason_codes` justifying the routing.
8. Save the JSON result to `output.json` in the current directory.

## Output schema

```json
{
  "ticket_id": "string (use filename stem if no explicit ID)",
  "team": "billing | legal | support | engineering | security | sales | hr",
  "priority": "low | medium | high | urgent",
  "category": "payment_failure | account_access | bug_report | contract_question | data_request | security_incident | renewal_risk | feature_request | general_inquiry",
  "sla_hours": "number",
  "requires_human_review": "boolean",
  "reason_codes": ["string", "..."],
  "confidence": "number between 0 and 1"
}
```

## Notes

- Cross-team tickets: pick the team that owns the *blocker*, set `requires_human_review: true`, and include both teams in `reason_codes`.
- Renewal-related payment issues are `renewal_risk` (not `payment_failure`) when a renewal date is mentioned.
- A "bug" mentioned by a non-technical user without an error or stack trace is usually `support`, not `engineering`.
- Lower confidence below 0.7 when the ticket is genuinely ambiguous between two teams.
