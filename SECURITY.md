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
- explicitly configure `CONCEPT_BRANCH_CORS_ORIGINS`;
- restrict network access and file-system permissions;
- back up the SQLite database and provider registry separately;
- do not share one operating-system account with mutually untrusted administrators.

The current release does not provide brute-force throttling, password recovery, external identity providers, multi-process database coordination, or an audited container deployment. Treat these as required design work before exposing the service to the public internet.

## Secret handling

- Provider keys must never be committed, pasted into issues, or stored in SQLite.
- Runtime keys are stored in per-user secret files with restrictive permissions.
- Logs must not contain prompts, selections, uploaded content, provider URLs, keys, or session tokens.
- Tests use recognizable synthetic credentials and a local mock provider only.

Run `bash scripts/verify.sh` before submitting a security-sensitive change.
