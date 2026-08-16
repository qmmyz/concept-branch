# Concept Branch

[简体中文](README.zh-CN.md)

Concept Branch is a self-hosted workspace for exploring AI conversations as a tree instead of a single linear chat. Select a passage from any answer, open a focused child branch, and keep the original discussion intact.

![Concept Branch workspace](docs/assets/concept-branch-workspace.png)

## Why this project exists

Long AI conversations often mix the main problem with useful side questions. Concept Branch gives each side question its own context while preserving where it came from. It is designed for research, technical reading, design exploration, and other work where traceability matters.

## Features

- Branch from selected Markdown text while retaining the source message and parent node.
- Search discussions, branches, and messages within the current account.
- Upload PDF, TXT, Markdown, CSV, JSON, and DOCX files as bounded context.
- Inherit file context from ancestor branches without duplicating uploads.
- Use OpenAI-compatible Chat Completions or Responses endpoints.
- Maintain multiple named providers and models per user.
- Isolate discussions, files, sessions, and provider credentials by user.
- Render Markdown, GitHub-flavored tables, code blocks, and KaTeX formulas.
- Run deterministic backend, isolation, attachment, build, and browser tests without a real API key.

## Engineering highlights

- **Authentication:** scrypt password hashing with per-user salts; opaque session tokens are stored only as SHA-256 hashes.
- **Tenant isolation:** every discussion, node, attachment, and provider lookup is scoped by authenticated user ID; cross-user lookups return `404`.
- **Credential boundary:** provider keys are kept outside SQLite and written atomically to `0600` files inside `0700` directories.
- **Bounded file handling:** 10 MB per file, PDF content streams capped at 4 MB each and 8 MB cumulatively, 50,000 extracted characters per file, and 60,000 injected characters per model request.
- **Failure-safe UI:** requests expose sent, waiting, and error states; failed input is restored for retry.
- **Reproducible verification:** pytest, Vite production build, and Playwright run against an isolated database and mock provider on dynamically allocated ports.

See [Architecture](docs/ARCHITECTURE.md) for component and trust-boundary details.

## Quick start

Requirements:

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+
- [ripgrep](https://github.com/BurntSushi/ripgrep) for the release verification script

```bash
uv sync
npm --prefix frontend ci
npm --prefix frontend run build
bash scripts/start_server.sh
```

Open <http://127.0.0.1:8421>. The first registered account is marked as the local administrator for future role-based features; v0.1 has no administrator-only controls. Add an OpenAI-compatible endpoint, model name, and API key from the provider settings screen.

Runtime data defaults to:

- `~/.local/share/concept-branch/concept-branch.sqlite3`
- `~/.config/concept-branch/`

Override these locations with `CONCEPT_BRANCH_DB` and `CONCEPT_BRANCH_CONFIG_DIR`. See [.env.example](.env.example) for all runtime variables.

## Development

```bash
uv sync
npm --prefix frontend ci
bash scripts/dev.sh
```

Run the complete local acceptance suite:

```bash
bash scripts/verify.sh
```

The browser suite automatically uses free localhost ports, so it can run while another Concept Branch instance is active.

## Deployment boundary

The default server listens on `127.0.0.1` and is intended for local use. For a trusted LAN, set `CONCEPT_BRANCH_HOST=0.0.0.0`. For any internet-facing deployment, put the app behind an HTTPS reverse proxy, set `CONCEPT_BRANCH_SECURE_COOKIES=1`, restrict allowed origins, and review [SECURITY.md](SECURITY.md).

This release is a single-node application. It does not yet include password recovery, external identity providers, distributed storage, or an internet-ready deployment bundle.

## Project status

`v0.1` is an actively maintained public preview. The core conversation, branching, authentication, isolation, provider, attachment, and browser workflows are covered by automated tests. Planned work includes localization, import/export, operational backups, and a hardened reverse-proxy deployment profile.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues should follow [SECURITY.md](SECURITY.md), not a public issue.

## License

[MIT](LICENSE)
