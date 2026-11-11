# Security Policy — RAG_Kit

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report it privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
go to the **Security** tab of this repository and choose **Report a
vulnerability**. That opens a private advisory visible only to the maintainers.

If you cannot use GitHub advisories, contact `<YOUR_CONTACT_EMAIL>` privately.

Please include:

- what the issue is and why it is exploitable
- the affected component (backend module, frontend route, or compose service)
- steps to reproduce, ideally a minimal request or file
- what an attacker gains

You should get an acknowledgement within a few days. Because this is a
volunteer-maintained template, please allow a reasonable window for a fix before
disclosing publicly.

## Supported versions

This project is a template, not a versioned product with a release train. Only
the current `main` branch receives security fixes. If you have forked it — which
is the intended use — you are responsible for pulling fixes into your fork.

| Version | Supported |
| ------- | --------- |
| `main`  | yes       |
| forks and older commits | no |

## What is in scope

The backend already implements the controls below. Bypasses of any of them are
in scope and genuinely useful to report:

- **Tenant isolation** — every vector query carries an `owner_id` filter applied
  in the query itself rather than as a post-filter
  (`backend/rag/retrieval.py`, `backend/rag/vectordb.py`). Anything that returns
  another user's chunks is the most serious class of bug here.
- **Upload validation** — extension allowlist, size cap, and magic-byte sniffing,
  plus filename sanitisation against path traversal
  (`backend/rag/security.py`).
- **SSRF guard** — ingested URLs are DNS-resolved and rejected when they point at
  private, loopback, link-local or cloud-metadata addresses. URLs discovered
  inside a sitemap are re-validated before fetching, so a redirect or a
  DNS-rebinding-style trick is worth testing.
- **Prompt-injection guards** — retrieved chunks and chat history are delimited
  and explicitly marked as untrusted data in the system prompt
  (`backend/rag/prompts.py`). An injection that makes the model ignore those
  delimiters, exfiltrate other context, or follow instructions embedded in an
  uploaded document is in scope.
- **Authentication and rate limiting** — JWT with rotation and blacklisting,
  per-scope DRF throttles on the chat and document endpoints.
- **Output rendering** — model output renders as React elements, never
  `dangerouslySetInnerHTML`; `javascript:` URLs are rendered as plain text.

## What is out of scope

- **The frontend login UI being absent.** This is documented, deliberate, and
  described in the README: the Next.js server signs in as a fixed account, so
  anyone who can open the app acts as that user. Do not deploy this build
  publicly as-is. Restoring per-user auth is a documented step, not a
  vulnerability report.
- **Default credentials in `.env.*.template` and `docker-compose.yaml`**
  (`minioadmin`, `change-password`). These are placeholders for local
  development and the README lists replacing them as a pre-deployment step.
- **Missing hardening that the README already tells you to enable before
  deploying** — `DEBUG=0`, `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`,
  `VECTOR_DB_API_KEY`, and the production Docker stage.
- Vulnerabilities in upstream dependencies with no exploitable path in this
  project — report those upstream. Dependabot already watches them.
- Cost-exhaustion by an authenticated user beyond the configured throttles, and
  anything requiring an attacker to already control the server or the model
  provider.

## Deploying this safely

Before exposing an instance to real users, work through the **Security** section
of the README. At minimum: restore per-user authentication, set `DEBUG=0` and a
strong `SECRET_KEY`, restrict `ALLOWED_HOSTS` and `CORS_ALLOWED_ORIGINS`, set
`VECTOR_DB_API_KEY`, replace the default MinIO credentials, and run the
production Docker stage rather than the development one.
