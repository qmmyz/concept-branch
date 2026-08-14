# Contributing

## Development setup

Requirements are Python 3.11+, uv, Node.js 22+, ripgrep (`rg`), and a Chromium browser available to Playwright.

```bash
uv sync
npm --prefix frontend ci
npm --prefix frontend exec -- playwright install chromium
bash scripts/dev.sh
```

No real model key is needed for tests.

## Before opening a pull request

```bash
bash scripts/verify.sh
```

The command runs pytest, a production frontend build, and the Playwright workflow with temporary storage and a local mock provider.

## Change expectations

- Add focused tests for API, authentication, isolation, attachment, or provider behavior changes.
- Preserve user scoping on every database lookup and mutation.
- Never log or return provider keys, session tokens, prompts, selections, or uploaded content.
- Keep model-generated text as data; do not execute it as code or shell input.
- Document new environment variables and deployment assumptions.
- Keep UI text concise and ensure desktop and mobile layouts remain usable.

Use conventional, scoped commit messages where practical. A pull request should describe the user-visible behavior, security impact, verification commands, and any migration requirements.
