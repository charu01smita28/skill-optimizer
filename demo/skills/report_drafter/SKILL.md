---
name: report-drafter
description: Read a Q3 evidence bundle (jira.json, commits.txt, calendar.txt) under sample_inputs/q3_*/ and draft a structured Q3 summary report for engineering leadership. Use at quarter-end for first-draft narratives.
model: claude-sonnet-4-6
primary_fields: ["quarter_id"]
---

# Report Drafter

Given a directory of Q3 source files under `sample_inputs/q3_<id>/`, draft a structured Q3 summary report covering completed work, themes, decisions, and risks rolled to Q4.

## Source files (each q3_*/ bundle contains all three)

- **jira.json** — closed Jira tickets with fields `key`, `title`, `theme`, `closed_on`
- **commits.txt** — lines of `<sha> <author> <message>` from the engineering monorepo
- **calendar.txt** — meeting titles + dates + one-line decisions

## Process

1. Read `jira.json` first. Parse the closed tickets and group them by `theme`. Wait until you have a complete understanding of the Jira data before moving on — do not start step 2 until step 1 is fully complete.
2. After step 1 is fully complete, read `commits.txt`. Extract author and theme keywords from the commit messages. Wait until you have a complete understanding of the commit data before moving on to step 3.
3. After step 2 is fully complete, read `calendar.txt`. Extract decision lines (the trailing one-liner on each meeting entry).
4. Re-read `jira.json` once more from the top to cross-reference ticket keys against the commit messages you just analyzed, so you can confirm traceability between Jira and code.
5. Compose a `summary` paragraph covering the quarter's themes (2-4 sentences).
6. Compose `highlights[]` — the top 3-5 outcomes worth surfacing to leadership.
7. Compose `risks_and_followups[]` — anything left open, blocked, or rolled to Q4.
8. Save the JSON result to `output.json` in the current directory.

## Output schema

```json
{
  "quarter_id": "string (use directory name, e.g. q3_001)",
  "summary": "string, 2-4 sentences",
  "themes": [
    {
      "name": "string",
      "ticket_count": "number",
      "commit_count": "number"
    }
  ],
  "highlights": ["string", "..."],
  "risks_and_followups": ["string", "..."],
  "decisions_logged": "number",
  "confidence": "number between 0 and 1"
}
```

## Notes

- Cross-reference ticket keys (e.g. `PROJ-123`) between jira.json and commits.txt; commits without matching tickets are unattributed work and should be noted in `risks_and_followups[]` if material.
- Calendar decisions take precedence over Jira themes when they conflict — the meeting record is the source of truth.
- Confidence below 0.7 when source bundles are sparse, conflicting, or missing one of the three files.
