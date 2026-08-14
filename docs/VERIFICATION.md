# Release verification

Candidate: `0.1.0`

Verification date: 2026-08-14

## Acceptance results

| Check | Result |
|---|---|
| Python tests | 43 passed |
| Frontend production build | passed |
| Playwright browser workflow | 1 passed |
| npm production dependency audit | 0 known vulnerabilities |
| Python dependency audit with pip-audit | 0 known vulnerabilities |
| Gitleaks 8.30.1 full public-history scan | 0 findings |
| Personal/internal marker allowlist | only the intended license copyright name |
| Root-external symlinks | none |

## Reproduction

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend exec -- playwright install chromium
bash scripts/verify.sh
npm --prefix frontend audit --omit=dev
```

The acceptance workflow serves the production frontend build through FastAPI under the release CSP, and uses temporary SQLite and provider directories, a synthetic account, a synthetic API key, and a local mock provider. No external model service or real credential is required.

## Scope boundary

This evidence covers deterministic code execution, isolation tests, the production frontend build, and the browser workflow. It does not claim that the preview is ready for direct internet exposure; the remaining deployment boundary is documented in `SECURITY.md`.
