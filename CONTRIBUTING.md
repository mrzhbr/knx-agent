# Contributing

Thank you for helping improve KNX Agent.

## Before opening a change

1. Open an issue for substantial behavior or architecture changes.
2. Never include real credentials, addresses, hostnames, coordinates, room
   topology, camera URLs, Matrix IDs, or runtime databases.
3. Preserve German user-facing text and the embodied first-person style.
4. Never weaken confirmation, authorization, or automation restrictions to make
   a test pass.

## Development

```bash
uv sync --frozen --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src --ignore-missing-imports --no-strict-optional
uv run pytest
```

Add focused tests for every behavior change. Security-sensitive changes should
cover denial paths, missing context, concurrency, replay, and direct-call bypasses.

Use conventional, focused commits. Explain user-visible behavior, validation,
and security implications in the pull request.

## Security reports

Follow [SECURITY.md](SECURITY.md); do not disclose vulnerabilities in public
issues or pull requests before a fix is available.
