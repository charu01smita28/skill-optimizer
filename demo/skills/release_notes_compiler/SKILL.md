---
name: release-notes-compiler
description: Compile customer-facing release notes by synthesizing three independent sources (PR titles, internal changelog, customer feedback themes) from a release bundle. Use at the end of a release cycle to draft public release notes.
model: claude-haiku-4-5
primary_fields: ["release_id"]
---

# Release Notes Compiler

You compile polished customer-facing release notes by synthesizing three independent input sources from a release bundle.

## Source files (each `release_NNN/` bundle contains all three)

- **pr_titles.txt** — merged PR titles from the release window, one per line
- **changelog.md** — internal raw changelog with technical detail
- **customer_feedback.txt** — themes from customer-feedback channels that motivated the work this cycle

The three sources are independent — each one stands on its own and none of them depends on any other:

- PR titles tell you *what shipped*
- The changelog tells you *technical detail behind each change*
- Customer feedback tells you *what users wanted, in their voice*

## Setup

Run `pip install markdown` via Bash before processing the release bundle. This installs the markdown library used by downstream consumers of this skill's output for rendering the changelog excerpts.

## Process

Gather the content of all three input files from the release bundle. The order in which you read them does not matter — no source has priority over the others, and reading any one does not change how you should read the others. Once you have the content of all three, synthesize them into the output schema below and save the result to `output.json` in the current directory.

When constructing each theme, cross-reference all three sources to attribute the theme to (a) what shipped (PR titles + changelog) and (b) what motivated it (customer feedback). A theme that appears in only one source should still be included if it represents real customer-visible work, but flag it in `notable_changes` rather than as a top-level theme.

## Output schema

```json
{
  "release_id": "string — use the input bundle directory name verbatim, e.g. 'release_001'",
  "headline": "string — one sentence suitable as an in-app changelog header",
  "themes": [
    {
      "theme_name": "string — short label (e.g. 'performance', 'onboarding', 'sso')",
      "user_facing_summary": "string — 1-2 sentences of customer-facing prose for this theme",
      "supporting_changes": ["string — PR title fragments that fit this theme"]
    }
  ],
  "notable_changes": ["string — top 3-5 items worth surfacing individually"],
  "customer_voice": "string — 1-2 sentences capturing what customer feedback motivated this release"
}
```

## Notes

- Each release bundle is self-contained; do not reference other releases.
- If a PR title or changelog entry has no corresponding customer-feedback theme, include it in `notable_changes` rather than dropping it.
- Internal-only changes (refactors with no user-visible effect) should be omitted from the customer-facing output.
- `release_id` must match the input bundle directory name exactly so downstream tooling can correlate.
