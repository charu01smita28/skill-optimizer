# Internal changelog — release_001

## Performance
- DB query batching for the dashboard endpoint reduces 95th-percentile latency from ~2.4s to ~800ms (about 67% faster).
- Redis cache for org settings; expected ~40% reduction on the settings API under typical workspace load.
- Removed an N+1 in the workspace-member listing endpoint; benchmarks show ~30% lower DB time on workspaces with 50+ members.

## Onboarding
- New signup flow: email-first verification, then a single profile step. Drops three of the prior five steps.
- First-run dashboard now surfaces 4 contextual tooltips on the most-used metrics (active users, retention, billing, integrations).
- Optional profile fields (avatar, role, team) deferred to a post-signup prompt rather than blocking signup completion.

## Internal
- Refactored auth middleware. No behavior change. Preparation for the SSO work landing in release_003.
- Billing job runner migrated to the new queue infra; better visibility, no user-facing impact.

## Docs
- API quickstart updated to reflect the new auth model.
