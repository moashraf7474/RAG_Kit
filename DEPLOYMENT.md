# Deployment

Running this stack in production on a single Docker host.

For what the system *is*, how ingestion and retrieval work, and how to tune
retrieval quality, see [README.md](README.md). This document only covers
getting it running for real users.

The development stack (`docker-compose.yaml`) is unchanged and keeps working
exactly as documented in the README — production is a separate, self-contained
file that never shares volumes with it.

---

## Table of contents

- [What is different in production](#what-is-different-in-production)
- [Prerequisites](#prerequisites)
- [DNS and domain setup](#dns-and-domain-setup)
- [Generating secrets](#generating-secrets)
- [First deploy](#first-deploy)
- [Creating the first admin user](#creating-the-first-admin-user)
- [Testing the production stack locally](#testing-the-production-stack-locally)
- [Backups and restore](#backups-and-restore)
- [Upgrading](#upgrading)
- [Scaling](#scaling)
- [Pre-flight security checklist](#pre-flight-security-checklist)
- [Troubleshooting](#troubleshooting)

---

## What is different in production

| | Development | Production |
|---|---|---|
| Compose file | `docker-compose.yaml` | `docker-compose.prod.yml` |
| Env file | `.env.backend`, `.env.frontend` | `.env.prod` |
| Django | `manage.py runserver` | gunicorn, non-root (uid 10001) |
| Next.js | `next dev` | `next build` + standalone server, non-root |
| Sources | bind-mounted from the host | baked into the image |
| Ingress | every service publishes a host port | only Caddy (80/443) |
| TLS | none | Let's Encrypt, automatic |
| `DEBUG` | `1` | `0` — enables HSTS, secure cookies, SSL redirect |
| Volumes | `postgres-data`, … | `postgres-data-prod`, … |

Because the volume names differ, production and development never share
state. You can run the dev stack on the same machine, though the ports will
collide with Caddy if you expose 80/443.

---

## Prerequisites

- A Linux host with Docker Engine 24+ and the Compose v2 plugin
- **4 GB RAM minimum**, 8 GB recommended. The worker holds embedding and
  rerank models in memory; `mem_limit` values in the compose file assume ~8 GB
  total.
- 20 GB disk to start. Vector indexes and stored documents both grow with
  corpus size.
- Ports **80 and 443 reachable from the internet** — Let's Encrypt validates
  over HTTP, so a firewall blocking port 80 means no certificate.
- A domain name you control.

---

## DNS and domain setup

Point an `A` record at the host's public IP **before** the first deploy. Caddy
requests a certificate on startup, and ACME validation fails if DNS has not
propagated yet.

```
rag.example.com.   A   203.0.113.10
```

Verify before continuing:

```bash
dig +short rag.example.com     # must print the host's public IP
```

Let's Encrypt applies [rate limits](https://letsencrypt.org/docs/rate-limits/)
(5 failures per account per hostname per hour). If DNS is wrong on the first
try, fix it and wait rather than restarting Caddy in a loop.

Certificate renewal is automatic and needs no cron job. Expiry notification
emails are off by default; to receive them, add a global block at the top of
the `Caddyfile`:

```caddyfile
{
	email ops@example.com
}
```

---

## Generating secrets

Copy the template and fill in every value marked `REQUIRED`:

```bash
cp .env.prod.template .env.prod
```

Generate each secret separately — never reuse one value across two variables:

```bash
openssl rand -base64 48    # SECRET_KEY
openssl rand -base64 48    # NEXTAUTH_SECRET
openssl rand -base64 36    # DATABASE_PASSWORD
openssl rand -base64 36    # VECTOR_DB_API_KEY
openssl rand -base64 24    # S3_ACCESS_KEY_ID
openssl rand -base64 24    # S3_SECRET_ACCESS_KEY
```

Then set the non-secret required values:

```ini
DOMAIN=rag.example.com
ALLOWED_HOSTS=rag.example.com,api
CORS_ALLOWED_ORIGINS=https://rag.example.com
CSRF_TRUSTED_ORIGINS=https://rag.example.com
NEXT_PUBLIC_APP_URL=https://rag.example.com
NEXTAUTH_URL=https://rag.example.com
LLM_API_KEY=sk-...
EMBEDDER_API_KEY=sk-...
```

`ALLOWED_HOSTS` must include `api` alongside your domain: the container
healthcheck and the frontend's server components reach Django by its service
name over the compose network.

Lock the file down — it holds every credential in the system:

```bash
chmod 600 .env.prod
```

`.env.prod` is gitignored. Never commit it.

---

## First deploy

```bash
make prod-up
```

That builds both production images and starts the stack. Equivalently:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

> The `--env-file .env.prod` flag is **required**. Compose interpolates
> `${DOMAIN}` and friends from `.env` by default, which in this repo holds dev
> host-port overrides. The `make` targets pass it for you.

First boot takes several minutes: it runs migrations, creates the Qdrant
collection and S3 bucket, and downloads the embedding and rerank models.

Watch it come up:

```bash
make prod-logs           # everything
make prod-logs S=api     # one service
```

Then confirm all services are healthy:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
```

Every service should read `healthy` (except `beat`, which has no healthcheck).
Verify from outside:

```bash
curl -fsS https://rag.example.com/api/health/
```

---

## Creating the first admin user

No user is seeded in production — the dev stack's `DEV_USERNAME` /
`DEV_PASSWORD` seeding is deliberately skipped when `DEV_PASSWORD` is unset.

```bash
make prod-superuser
```

Then sign in at `https://rag.example.com/admin/`.

---

## Testing the production stack locally

Set `DOMAIN=localhost` in `.env.prod`. Caddy then issues its own internal
certificate instead of contacting Let's Encrypt — no DNS and no public ports
needed:

```ini
DOMAIN=localhost
ALLOWED_HOSTS=localhost,api
CORS_ALLOWED_ORIGINS=https://localhost
CSRF_TRUSTED_ORIGINS=https://localhost
NEXT_PUBLIC_APP_URL=https://localhost
NEXTAUTH_URL=https://localhost
```

```bash
make prod-up
curl -k https://localhost/api/health/
```

Your browser will warn about the self-signed certificate; that is expected.
Stop the dev stack first if it is running — both want port 80/443 and 3000.

---

## Backups and restore

`scripts/backup.sh` captures all three stateful services into one timestamped
directory:

```bash
make backup                  # writes backups/<timestamp>/
```

| File | Contents | Restored with |
|---|---|---|
| `postgres.dump` | Documents, users, chat history, Celery results | `pg_restore` |
| `qdrant.snapshot` | The vector collection | Qdrant snapshot recovery API |
| `minio-objects/` | Original uploaded files | `mc mirror` |
| `MANIFEST` | Database, collection and bucket names | — |

All three belong to one logical backup. Restoring Postgres without the
matching Qdrant snapshot leaves documents whose chunks no longer exist.

Restore is destructive and prompts before doing anything:

```bash
make restore B=backups/20250101-120000
```

It stops `api`, `worker` and `beat`, replaces the data, then restarts them.
Set `FORCE=1` to skip the prompt in automation.

Schedule it with cron and keep copies off the host:

```cron
0 3 * * * cd /srv/rag-kit && ./scripts/backup.sh >> /var/log/rag-backup.log 2>&1
```

Test a restore on a non-production host before you need one. An untested
backup is a guess.

---

## Upgrading

```bash
git pull
make backup                  # always, before migrations
make prod-up                 # rebuilds and recreates changed services
```

`prod-up` runs `migrate` automatically as part of the api container's startup.

**Qdrant version bumps need care.** Qdrant storage is not forward compatible,
and the image is pinned in step with `qdrant-client` in
`backend/pyproject.toml`. To move across a minor version: back up, change both
pins together, wipe the `qdrant-data-prod` volume, redeploy, then restore the
snapshot.

Changing `EMBEDDER_MODEL` or `EMBEDDER_DIMENSIONS` invalidates every stored
vector and requires re-ingesting the whole corpus.

---

## Scaling

**Worker concurrency.** `CELERY_CONCURRENCY` (default 2) is the number of
documents ingested in parallel. Ingestion is CPU-bound whenever reranking or
sparse embeddings run locally, so raising it past the host's core count makes
throughput worse, not better. Each worker process also holds its own copy of
the models — budget roughly 1 GB of RAM per unit of concurrency and raise the
`worker` `mem_limit` alongside it.

Scale out instead of up when one host is saturated:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --scale worker=3
```

**Do not scale `beat` beyond one replica** — two schedulers mean every
periodic task fires twice.

**Gunicorn.** `GUNICORN_WORKERS` defaults to 4; the usual starting point is
`(2 × cores) + 1`. Chat responses stream, so threads matter more than
processes here: `GUNICORN_THREADS=2` lets each worker hold multiple open
streams.

**Scaling `api` past one replica** requires moving `migrate` out of the api
startup command into a one-shot job, otherwise replicas race each other on
boot. This is flagged in `docker-compose.prod.yml`.

**Hosted embedder vs local FastEmbed.** The biggest lever on worker cost.

| | Hosted (OpenAI-compatible) | Local (FastEmbed / FlashRank) |
|---|---|---|
| Set | `EMBEDDER_API_KEY`, `EMBEDDER_BASE_URL` | `EMBEDDER_BASE_URL` at a local model |
| CPU | Minimal | High — dominates ingestion time |
| RAM | ~1 GB worker | 2–4 GB worker |
| Cost | Per token | Per host |
| Data | Leaves your network | Stays in it |

A hosted embedder lets a small VM handle far more ingestion; local models cost
nothing per token and keep documents in-house. `SPARSE_EMBEDDER_ENABLED` and
`RERANKER_ENABLED` always run locally on CPU regardless — turning them off
lowers RAM and CPU at a real cost to retrieval quality.

For heavy corpora, run the worker on a separate machine from the API rather
than making one host bigger.

---

## Pre-flight security checklist

Before pointing real users at it:

- [ ] `DEBUG=0` in `.env.prod` — everything below depends on it
- [ ] `SECRET_KEY` and `NEXTAUTH_SECRET` freshly generated, not copied from dev
- [ ] `DATABASE_PASSWORD` is not `change-password`
- [ ] `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` are **not** `minioadmin`
- [ ] `VECTOR_DB_API_KEY` set — Qdrant rejects unauthenticated requests
- [ ] `ALLOWED_HOSTS` lists your real domain (plus `api`), no wildcard
- [ ] `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` are `https://` and exact
- [ ] `chmod 600 .env.prod`, and it is not committed
- [ ] `DEV_USERNAME` / `DEV_PASSWORD` unset
- [ ] `THROTTLE_*` tuned — every chat call and upload costs real money
- [ ] `INGESTION_ALLOWED_URL_SCHEMES=https` if you ingest public URLs (limits SSRF)
- [ ] `https://` works and `http://` redirects to it
- [ ] No data service publishes a host port: `docker compose ... ps` should show
      published ports only for `caddy`
- [ ] A backup has been taken **and test-restored**
- [ ] Host firewall allows only 22, 80 and 443

With `DEBUG=0` Django also enables HSTS (1 year, preload), secure and
HTTP-only cookies, `X-Frame-Options: DENY` and `nosniff`. See the "Production
security" block in `backend/api/settings.py` and the
[Security section of the README](README.md#security).

---

## Troubleshooting

**Caddy cannot get a certificate.** Check `make prod-logs S=caddy`. Almost
always DNS not resolving to this host, or port 80 blocked upstream. Confirm
with `dig +short $DOMAIN`. Mind the rate limits while retrying.

**`DisallowedHost` in the api logs.** Your domain is missing from
`ALLOWED_HOSTS`.

**CSRF failures in the admin.** `CSRF_TRUSTED_ORIGINS` must include the
`https://` origin, scheme included.

**Redirect loop.** Caddy already terminates TLS and Django trusts
`X-Forwarded-Proto`. If you put another proxy or CDN in front, it must forward
that header too.

**Qdrant returns 401/403.** `VECTOR_DB_API_KEY` in `.env.prod` must match what
the container was started with. Changing it requires recreating `qdrant`.

**Worker OOM-killed during ingestion.** Lower `CELERY_CONCURRENCY`, or raise
the `worker` `mem_limit`. Large PDFs with `INGESTION_PREFER_DOCLING=True` are
the usual cause.

For non-deployment issues, see the
[Troubleshooting section of the README](README.md#troubleshooting).
