# RAG_Kit

RAG_Kit is a document-focused conversational search application that lets me chat with my own files and receive answers with citations to the relevant source passages. It combines document ingestion, search, and a chat interface so I can find precise answers in my knowledge base without manually scanning through every document.

## How the app works (concepts)

Retrieval-Augmented Generation (RAG) combines search with a language model:

1. **Chunking:** Documents are split into smaller passages. Smaller passages are easier to search and give the model focused context.
2. **Embeddings:** An embedding model turns each passage into a vector, a list of numbers that represents its meaning.
3. **Vector database:** Qdrant stores those vectors and passage metadata. The metadata connects each passage to its document, page, and organization.
4. **Hybrid search:** RAG_Kit can combine dense vector search for meaning with sparse search for exact words and identifiers.
5. **Reranking:** A cross-encoder scores the search results against the question and moves the most relevant passages to the top.
6. **Generation:** The chat model receives the question and retrieved passages. The prompts instruct it to answer from that context rather than inventing unsupported facts.
7. **Citations:** The API returns the passages used to answer. The chat UI shows their source document and location.

## Architecture

```text
Browser
  |
  v
Next.js web app ---- server-side API requests ----> Django REST API
  |                                                  |       |
  |                                                  |       +--> PostgreSQL
  |                                                  |            users, documents,
  |                                                  |            chunks, conversations
  |                                                  |
  |                                                  +--> Qdrant
  |                                                  |    dense and sparse vectors
  |                                                  +--> MinIO
  |                                                       original uploaded files
  |
  +<---- streamed chat response ---------------------+

                         Django task queue
                  Django API --> Redis --> Celery worker
                                           |       |
                                           |       +--> MinIO
                                           +-----------> Qdrant

              LangChain components + LangGraph chat graph
                         |
                         +--> optional Langfuse traces
```

| Component | Why it exists |
|---|---|
| Next.js | Provides the web pages, authentication flow, document UI, and streaming chat interface. |
| Django / DRF | Serves the API, validates requests, handles authentication and permissions, and stores application records. |
| Celery | Runs ingestion work outside the request cycle. |
| Redis | Acts as the Celery broker and supports the configured Django cache. |
| PostgreSQL | Stores users, organizations, documents, chunks, conversations, messages, and ingestion reports. |
| Qdrant | Stores embeddings and supports filtered dense and hybrid search. |
| MinIO | Stores original uploaded files through its S3-compatible API. |
| LangChain / LangGraph | LangChain connects models and prompts; LangGraph sequences the chat steps. |
| Langfuse | Optional tracing for model and retrieval work. Its services run with the `observability` Compose profile. |

The normal Compose stack also includes a Celery Beat process for periodic tasks. The optional profiles add Ollama for local model serving or Langfuse and its supporting services for observability.

## How a document is ingested

1. The API validates the upload, writes the original file to MinIO, creates a document record in PostgreSQL, and queues a Celery task.
2. [`extract.py`](backend/rag/extract.py) reads file content or fetches a supported URL, sitemap, or Confluence source. File extraction tries the configured extractors and uses plain-text handling for `.txt`, `.md`, and `.rst`.
3. [`chunking.py`](backend/rag/chunking.py) splits text while retaining useful page and heading metadata. Tables are kept whole.
4. [`ingest.py`](backend/rag/ingest.py) coordinates the ingestion stages, writes indexed chunks to PostgreSQL, and records status and diagnostics.
5. [`vectordb.py`](backend/rag/vectordb.py) embeds the chunks and upserts them into Qdrant. Stable IDs make re-ingestion replace existing vectors rather than duplicate them.
6. If enrichment is enabled, [`enhance.py`](backend/rag/enhance.py) creates page summaries. Image captioning is handled by `image_captioner.py`. The worker indexes these additional chunks and marks the document ready.

The Celery task first indexes text and tables. Optional summaries and image captions run as enrichment work. This means text can become searchable before enrichment finishes.

## How a question is answered

The chat graph is built in [`graph.py`](backend/rag/graph.py):

```text
START
  ├─ small talk ------------------------------> END
  └─ determine_language -> rephrase -> retrieve
                                      ├─ results -> generate -> END
                                      └─ no results/error -> error_node -> END
```

1. The small-talk gate in `graph.py` handles configured greetings without retrieval.
2. `determine_language` identifies the question language. `rephrase` uses recent chat history to make a follow-up question searchable on its own.
3. `retrieve` in [`retrieval.py`](backend/rag/retrieval.py) searches each configured content type concurrently, expands matching summaries to their source chunks, removes duplicates, and applies a result limit.
4. [`rerank.py`](backend/rag/rerank.py) uses FlashRank to reorder the candidates and keep the strongest matches.
5. The graph's `generate` node uses the answer prompts in [`prompts.py`](backend/rag/prompts.py) with the question, history, and retrieved passages. If retrieval fails or finds no usable passages, `error_node` returns a user-facing message instead.
6. `graph.py` shapes retrieved passages as citations. Streaming sends citations before answer tokens so the UI can show sources while the answer arrives.

## How the project is structured

This is a walkthrough of the project structure and implementation:

1. **Infrastructure — `docker-compose.yaml`:** Start with PostgreSQL, Qdrant, Redis, and MinIO. The same file defines the Django API, Celery worker and Beat process, Next.js web app, and optional service profiles. `docker-compose.prod.yml` describes the production stack.
2. **Backend API — `backend/api/`:** Django settings, URLs, authentication, health checks, and Celery configuration provide the application boundary. `backend/rag/` contains the Django models, API views, and RAG code.
3. **Ingestion — `backend/rag/extract.py`, `chunking.py`, `ingest.py`, and `vectordb.py`:** The API saves a source and queues a task; the worker extracts, chunks, embeds, stores, and reports the result.
4. **Retrieval — `backend/rag/retrieval.py` and `rerank.py`:** Searches are scoped to the caller's organization and document visibility, then deduplicated and ranked.
5. **Chat graph — `backend/rag/graph.py` and `prompts.py`:** LangGraph coordinates language detection, query rewriting, retrieval, generation, error handling, and citations.
6. **Frontend — `frontend/apps/web/`:** Next.js pages and components provide sign-in, registration, document management, and chat. The server-side API client and chat route communicate with Django.

To recreate these layers, first provide the infrastructure endpoints and secrets through the environment templates. Then start the API and worker against those services. The frontend uses the API URL and authentication settings from its environment. The production Compose file adds Caddy for public HTTP/HTTPS ingress.

## Run it

### Prerequisites

- Git
- Docker Engine with Docker Compose v2
- An OpenAI-compatible chat model and API key, or Ollama for local generation
- OpenSSL to generate a Django secret

You do not need a local Python or Node.js installation to run the Docker Compose app.

### Clone and configure

Replace `OWNER` with the GitHub account or organization that hosts your RAG_Kit repository:

```bash
git clone https://github.com/OWNER/rag-kit.git
cd rag-kit
cp .env.backend.template .env.backend
cp .env.frontend.template .env.frontend
```

Generate a secret and paste its output into `SECRET_KEY` in `.env.backend`:

```bash
openssl rand -base64 48
```

For a hosted model, set `LLM_API_KEY` in `.env.backend`. The backend template uses a local FastEmbed embedding model by default, so an embedding API key is not needed for that configuration. If you choose a hosted embedding model, set its provider, API key, model, and matching `EMBEDDER_DIMENSIONS`.

### Start with a hosted model

Set the model and key in `.env.backend`, then start the stack:

```bash
docker compose up -d
```

### Start with Ollama

The Ollama profile runs the local model service. Pull the model, then set `LLM_BASE_URL=http://ollama:11434/v1` and `LLM_MODEL=llama3.1` in `.env.backend`:

```bash
docker compose --profile local-llm up -d
docker compose exec ollama ollama pull llama3.1
```

The model download and the default local embedding model may need an initial network connection or pre-populated caches.

### Create an account and open the app

New registrations are inactive until a Django administrator activates them. Create an administrator first:

```bash
docker compose exec api uv run python manage.py createsuperuser
```

Open [http://localhost:8000/admin/](http://localhost:8000/admin/) and sign in as that administrator. Then open [http://localhost:3000/register](http://localhost:3000/register) to register an account. Return to the Django admin and activate that account. Sign in at [http://localhost:3000](http://localhost:3000), upload a document, wait for it to become ready, and ask a question in Chat.

| Service | URL |
|---|---|
| RAG_Kit web app | http://localhost:3000 |
| Django API | http://localhost:8000/api/ |
| Swagger UI | http://localhost:8000/api/schema/swagger-ui/ |
| Django admin | http://localhost:8000/admin/ |
| Health check | http://localhost:8000/api/health/ |
| Qdrant dashboard | http://localhost:6333/dashboard |
| MinIO console | http://localhost:9001 |
| Langfuse (optional profile) | http://localhost:3001 |

### Stop or remove the stack

Stop and remove containers while keeping data volumes:

```bash
docker compose down
```

Remove containers and all Compose data volumes:

```bash
docker compose down -v
```

The second command permanently removes the local database, vectors, uploaded files, and Redis data.

## Configuration

The backend settings are in `.env.backend.template`; frontend settings are in `.env.frontend.template`. Production uses `.env.prod.template`.

| Variable | Template value | Purpose |
|---|---|---|
| `SECRET_KEY` | Empty | Django signing key. Generate a unique value. |
| `DEBUG` | `1` in backend template; `0` in production template | Development diagnostics and production security behavior. |
| `LLM_MODEL` | `gpt-4o-mini` | Chat and generation model name. |
| `LLM_API_KEY` | Empty | Chat provider credential. Ollama can use a placeholder-free local configuration. |
| `LLM_BASE_URL` | Empty | Optional OpenAI-compatible API URL, such as Ollama's internal URL. |
| `EMBEDDER_PROVIDER` | `local` | Embedding provider (`local` uses FastEmbed; hosted configuration is also supported). |
| `EMBEDDER_MODEL` | `BAAI/bge-small-en-v1.5` | Embedding model in the backend template. |
| `EMBEDDER_DIMENSIONS` | `384` | Must match the selected model; changing it requires re-indexing. |
| `VECTOR_DB_URL` | `http://qdrant:6333` | Qdrant service URL. |
| `VECTOR_DB_COLLECTION_NAME` | `rag` | Qdrant collection name. |
| `VECTOR_DB_RETRIEVAL_MODE` | `HYBRID` | `HYBRID`, `DENSE`, or `SPARSE` search mode. |
| `RETRIEVER_THRESHOLD` | `0.5` | Minimum similarity score for text results. |
| `RETRIEVER_TOTAL_K_DOCUMENTS` | `10` | Maximum candidates before reranking. |
| `RERANKER_K_DOCUMENTS` | `5` | Maximum ranked passages retained. |
| `CHUNKER_MAX_SIZE` | `1000` | Target characters per text chunk. |
| `CHUNKER_OVERLAP` | `100` | Overlap between adjacent text chunks. |
| `SUMMARIZER_ENABLED` | `True` | Adds page summaries during enrichment; uses model calls. |
| `INGESTION_MAX_FILE_SIZE_MB` | `50` | Maximum uploaded file size. |
| `THROTTLE_CHAT` | `120/hour` | Per-user chat request rate limit. |
| `S3_ENDPOINT` | `http://minio:9000` | S3-compatible object storage endpoint. |
| `S3_BUCKET` | `rag-documents` | Bucket for original uploaded files. |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis broker used by Celery. |
| `NEXTAUTH_SECRET` | Empty | Secret used to sign frontend session cookies. |
| `NEXT_PUBLIC_APP_URL` | `http://localhost:3000` | Public frontend URL. |
| `API_URL` | `http://api:8000` | Django API address used by the frontend server. |
| `DEV_MODE` | Empty | Leave unset for normal registration and sign-in; local-only shared-account mode when enabled. |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Empty | Optional tracing credentials. |

Use the production template for production defaults and required secrets. Do not copy development credentials into a public deployment.

## Security notes

- Do not commit `.env.backend`, `.env.frontend`, or `.env.prod`. Keep API keys, passwords, and generated secrets private.
- Do not expose the development Compose stack to the public internet. It publishes development service ports.
- Before public deployment, set `DEBUG=0`, use strong unique secrets, restrict `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, and `CSRF_TRUSTED_ORIGINS`, and use the production Compose file.
- Set a Qdrant API key and the production storage/database credentials. Replace all development defaults.
- Keep `DEV_MODE` empty. It is intended only for local experiments and signs every visitor in as one shared account.
- The backend validates file types and sizes, sanitizes names, checks URLs against private destinations, applies API throttles, and scopes retrieval to organization and document visibility. Keep these controls enabled.
- Retrieved document text is untrusted input. Do not weaken the prompt injection safeguards in `backend/rag/prompts.py`.
- Review `SECURITY.md` and `DEPLOYMENT.md` before exposing the service publicly.

## Troubleshooting

**The API cannot reach the model provider.** Check `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` in `.env.backend`, then restart the API and worker:

```bash
docker compose restart api worker
```

**Chat says the knowledge base is empty.** Check that a document has reached `READY` in the Documents page. Review worker logs:

```bash
docker compose logs worker
```

**A ready document does not appear in search.** Confirm the embedding model and `EMBEDDER_DIMENSIONS` match the current Qdrant collection. Changing embedding models requires re-indexing documents.

**A port is already in use.** Host ports can be overridden in the root `.env` file. Check the `*_HOST_PORT` settings and choose a free port, then restart Compose.

**A container exits or restarts repeatedly.** Review its logs and Docker resource allocation:

```bash
docker compose logs api worker
docker compose ps
```

**The observability profile does not start.** Set `LANGFUSE_ENCRYPTION_KEY` in `.env.backend`, then pass that env file for Compose interpolation:

```bash
openssl rand -hex 32
docker compose --env-file .env.backend --profile observability up -d
```

## License

RAG_Kit is released under the MIT License; see [LICENSE](LICENSE).
