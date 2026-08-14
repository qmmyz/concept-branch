# Security policy

## Supported versions

Security fixes currently target the latest tagged release and the default branch.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting for this repository. Include the affected version, reproduction steps, impact, and any suggested mitigation. Do not include real API keys, session cookies, uploaded documents, or personal data.

## Deployment boundary

Concept Branch defaults to localhost. The public preview is not an internet-ready identity platform.

For any deployment beyond localhost:

- use an HTTPS reverse proxy;
- set `CONCEPT_BRANCH_SECURE_COOKIES=1`;
- explicitly configure `CONCEPT_BRANCH_CORS_ORIGINS` only for trusted browser origins; the default allowlist is empty;
- enforce an upload body-size limit at the reverse proxy as well as the application's extracted-file limit;
- restrict network access and file-system permissions;
- back up the SQLite database and provider registry separately;
- do not share one operating-system account with mutually untrusted administrators.

The current release does not provide brute-force throttling, password recovery, external identity providers, CSRF tokens, multi-process database coordination, or an audited container deployment. SameSite cookies, an empty default CORS allowlist, and same-origin browser APIs are the v0.1 cross-site request boundary; do not weaken those defaults. Registration reports an existing username with HTTP 409. Treat throttling, non-enumerating account flows, dedicated CSRF protection, and an ingress request-body limit as required design work before exposing the service to the public internet.

Provider URLs are intentionally user-configurable and cause the server to make outbound requests. On a shared or LAN deployment, only trusted users should be allowed to configure providers; otherwise those URLs can become an SSRF path to services reachable by the host.

## Secret handling

- Provider keys must never be committed, pasted into issues, or stored in SQLite.
- Runtime keys are stored in per-user secret files with restrictive permissions.
- Logs must not contain prompts, selections, uploaded content, provider URLs, keys, or session tokens.
- Tests use recognizable synthetic credentials and a local mock provider only.
- Static frontend routes resolve requested files and reject any target outside the packaged frontend root, including symlink escapes.
- Application responses set a restrictive content security policy, deny framing, disable MIME sniffing, and limit referrer and browser-feature exposure. HSTS is enabled with secure cookies for HTTPS deployments.

Run `bash scripts/verify.sh` before submitting a security-sensitive change.
