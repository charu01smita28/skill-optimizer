# Internal changelog — release_003

## SSO
- SAML 2.0 SSO with certified configurations for Okta and Azure AD. Custom-IdP setup is available via the workspace SSO settings page.
- Token rotation now follows per-workspace policy (default 12 hours, configurable from 1h to 24h).
- SCIM 2.0 user provisioning endpoint live; supports create / update / deactivate. Group-based role mapping is on the roadmap (not in this release).

## Roles
- Custom workspace role definitions: bring your own permission set rather than choosing one of the four fixed presets (admin / member / viewer / billing-only).
- Existing workspaces continue to use the fixed presets unless an admin opts in to custom roles. No automatic migration.

## Performance
- Session cache reduces auth lookup load by ~80%. Cache is invalidated on session revoke or password change.
- Signed-URL generation moved out of the request hot path; reduces p99 latency for download endpoints by ~120ms.

## Docs
- SSO setup guide with annotated screenshots for Okta and Azure AD admins.
