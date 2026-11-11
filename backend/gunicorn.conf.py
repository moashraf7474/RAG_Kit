"""Gunicorn config for the api service.

A config file avoids CLI-flag parsing ambiguity across the
bash -c / uv run / gunicorn process chain used in docker-compose.yaml.

One worker: the process caches the embedder and reranker models in memory
(see rag/llm.py get_embedder, @lru_cache) — a second worker process would
duplicate that memory cost, not add real capacity, on a memory-constrained
host. Threads give real concurrency for the I/O-bound work (LLM calls,
Qdrant, S3) without paying for a second model load.
"""

bind = "0.0.0.0:8000"
workers = 1
worker_class = "gthread"
threads = 4
# A chat request waits on retrieval + an LLM call; give it real room.
timeout = 120
