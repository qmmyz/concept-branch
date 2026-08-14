# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-08-14

Initial public preview.

### Added

- Tree-structured discussions created from selected source text.
- Multi-user registration, login, sessions, and tenant-scoped storage.
- Per-user OpenAI-compatible provider and model registry.
- PDF, TXT, Markdown, CSV, JSON, and DOCX attachment context with inheritance and explicit bounds.
- Discussion and message search, resizable panels, collapsible navigation, and light/dark modes.
- Deterministic pytest, Vite build, and Playwright acceptance workflow with a local mock provider.
- English and Chinese documentation, architecture notes, security policy, contribution guide, and CI.

### Security and reliability

- Provider keys remain outside SQLite and are written atomically with restrictive permissions.
- Session tokens are stored as hashes and sent in HttpOnly, SameSite cookies; Secure cookies are configurable for HTTPS.
- Browser acceptance tests allocate isolated ports instead of colliding with a running instance.
- Added the missing tenant-scoped `GET /api/discussions/{id}` route exposed by clean-environment verification.
- Static frontend routes reject encoded traversal and symlink escapes instead of serving files outside `frontend/dist`.
- Expired sessions are pruned during authentication, and provider updates reject empty model lists.
- Browser responses include baseline CSP, framing, MIME, referrer, permissions, and HTTPS HSTS headers.
- Public documentation now matches the verified test count and accurately scopes the reserved administrator role.
- The CSP-compatible launcher uses an external script, and Playwright now exercises the FastAPI-served production build under the release response headers.
- Default CORS permissions are empty; the development script opts into only its dynamic Vite origin.
- Verification now fails when ripgrep is unavailable instead of silently skipping its secret-like-value scan.
- Added attachment-bound, DOCX declaration, database-permission, and default-CORS regression coverage.
- SQLite is restricted to `0600` inside a `0700` directory, and the Classic route returns `404` when its build is absent.
