# Contributing to RAG_Kit

Thanks for considering a contribution. RAG_Kit is a RAG application, so the
bar for changes is slightly unusual: features should be things most people
building a RAG product would want. Anything narrower is better as a fork or a
documented extension point.

## Getting a dev environment

### Docker (recommended)

Nothing needed on your machine except Docker.

```bash
make init            # copies the .env templates and generates secrets
# set LLM_API_KEY and EMBEDDER_API_KEY in .env.backend
docker compose up
make superuser       # create the account the frontend signs in as
```

Set `DEV_USERNAME` / `DEV_PASSWORD` in `.env.frontend` to match that account.
The frontend is at http://localhost:3000, API docs at
http://localhost:8000/api/schema/swagger-ui/.

### Without Docker

You still need Postgres, Qdrant, Redis and MinIO. The easiest path is to run the
infrastructure in Docker and the app on your machine:

```bash
docker compose up -d db redis qdrant minio
```

Backend — Python 3.13 with [uv](https://docs.astral.sh/uv/):

```bash
cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py init_rag      # Qdrant collection + S3 bucket
uv run python manage.py runserver
```

Frontend — Node 22 with pnpm 9:

```bash
cd frontend
pnpm install -r
pnpm --filter rag-kit-web dev
```

The backend reads its configuration from the environment. When running outside
Docker the service hostnames differ, so export the localhost equivalents (see
the `test` target in the `Makefile` for the exact set).

## Running tests

```bash
make test
```

which is:

```bash
cd backend && SECRET_KEY=test DATABASE_HOST=localhost DATABASE_PORT=5433 \
  VECTOR_DB_URL=http://localhost:6333 S3_ENDPOINT=http://localhost:9000 \
  uv run pytest rag/tests/ api/tests/ -q
```

52 tests at time of writing. Integration tests **silently skip** when Qdrant or
MinIO are unreachable, so if your run finishes suspiciously fast, start the
infrastructure containers first — a skip looks a lot like a pass.

New behaviour needs a test. Security-relevant behaviour (upload validation, the
SSRF guard, tenant isolation) needs a test that fails without the fix.

## Code style

Formatting and linting are enforced by pre-commit, so install it once:

```bash
pre-commit install --install-hooks
pre-commit install --hook-type commit-msg
```

- **Python** — ruff (lint + format), 88-column lines. Config in
  `backend/pyproject.toml`.
- **TypeScript / React** — Biome. Single quotes, no semicolons, 2-space indent,
  80 columns. Config in `frontend/biome.json`.
- **CSS** — Tailwind. **Never use `font-bold` or any weight above
  `font-medium`.** Build hierarchy with size and colour contrast instead.

### Comments explain *why*, not *what*

The code already says what it does. A comment earns its place by recording the
reason a reader could not otherwise recover — a constraint, a trade-off, a
non-obvious failure mode. The existing modules follow this; match them.

```python
# Good: records a constraint you cannot infer from the code
# Fail soft, exactly as upstream does: unreranked beats no answer.

# Bad: restates the line below it
# Loop over the documents
```

Docstrings on ported modules name their upstream origin. If you move ported
code, keep that attribution — it is a license obligation, not decoration (see
`NOTICE`).

## Commits

[Conventional Commits](https://www.conventionalcommits.org/), enforced by a
`commit-msg` hook:

```
feat: add reranker score to citation payload
fix: reject sitemap URLs resolving to link-local addresses
docs: document EMBEDDER_DIMENSIONS mismatch symptoms
```

Types in use: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Pull requests

1. Open an issue first for anything substantial. A rejected 800-line PR is a bad
   day for everyone.
2. Branch off `main`.
3. Keep the PR to one concern. Two unrelated fixes are two PRs.
4. Make sure `make test` passes and pre-commit is clean.
5. Fill in the PR template — particularly how you tested the change.
6. Update `README.md` if you changed behaviour, configuration, or the test count.

CI runs backend lint and tests against real Postgres, Qdrant, Redis and MinIO,
builds the frontend, and builds both Docker images. It needs to be green.

### Changes that need extra care

- **`backend/rag/prompts.py`** — the injection guards are load-bearing security
  controls. Do not soften them.
- **Anything touching `owner_id` filtering** — that is the tenant isolation
  boundary.
- **Qdrant version bumps** — Qdrant storage is not forward compatible. The image
  in `docker-compose.yaml` and `qdrant-client` in `backend/pyproject.toml` move
  together, or not at all.
- **Ported files** — if a change diverges from upstream behaviour deliberately,
  say so in the docstring like the existing ones do.
