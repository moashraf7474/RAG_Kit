.DEFAULT_GOAL := help
COMPOSE := docker compose

# Production runs from its own compose file and env file. `--env-file` is not
# optional: without it compose interpolates ${DOMAIN} and friends from .env
# (dev host ports) instead of .env.prod.
COMPOSE_PROD := docker compose --env-file .env.prod -f docker-compose.prod.yml

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: init
init: ## Create .env files with generated secrets
	@test -f .env.backend.template || (echo "FATAL: .env.backend.template missing" && exit 1)
	@test -f .env.frontend.template || (echo "FATAL: .env.frontend.template missing" && exit 1)
	@test -f .env.backend  || (cp .env.backend.template .env.backend && \
		python3 -c "import re,pathlib,secrets,base64;p=pathlib.Path('.env.backend');p.write_text(re.sub(r'^SECRET_KEY=$$','SECRET_KEY='+base64.b64encode(secrets.token_bytes(36)).decode(),p.read_text(),flags=re.M))" && \
		echo "created .env.backend")
	@test -f .env.frontend || (cp .env.frontend.template .env.frontend && \
		python3 -c "import re,pathlib,secrets,base64;p=pathlib.Path('.env.frontend');p.write_text(re.sub(r'^NEXTAUTH_SECRET=$$','NEXTAUTH_SECRET='+base64.b64encode(secrets.token_bytes(24)).decode(),p.read_text(),flags=re.M))" && \
		echo "created .env.frontend")
	@echo "Now set LLM_API_KEY and EMBEDDER_API_KEY in .env.backend."

.PHONY: up
up: ## Start the core stack
	$(COMPOSE) up -d
	@echo "frontend  http://localhost:$${WEB_HOST_PORT:-3000}"
	@echo "api docs  http://localhost:$${API_HOST_PORT:-8000}/api/schema/swagger-ui/"

.PHONY: down
down: ## Stop everything
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop everything and delete all data volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs (S=service to filter)
	$(COMPOSE) logs -f $(S)

.PHONY: health
health: ## Check dependency health
	@curl -s http://localhost:$${API_HOST_PORT:-8000}/api/health/ | python3 -m json.tool

.PHONY: superuser
superuser: ## Create a Django superuser
	$(COMPOSE) exec api uv run python manage.py createsuperuser

.PHONY: shell
shell: ## Django shell
	$(COMPOSE) exec api uv run python manage.py shell

.PHONY: migrate
migrate: ## Apply migrations
	$(COMPOSE) exec api uv run python manage.py migrate

.PHONY: makemigrations
makemigrations: ## Generate migrations
	$(COMPOSE) exec api uv run python manage.py makemigrations

.PHONY: init-rag
init-rag: ## Create the Qdrant collection and S3 bucket
	$(COMPOSE) exec api uv run python manage.py init_rag

.PHONY: test
test: ## Run the backend test suite
	cd backend && SECRET_KEY=test DATABASE_HOST=localhost \
		DATABASE_PORT=$${DATABASE_HOST_PORT:-5433} \
		VECTOR_DB_URL=http://localhost:$${QDRANT_HOST_PORT:-6333} \
		S3_ENDPOINT=http://localhost:$${MINIO_HOST_PORT:-9000} \
		uv run pytest rag/tests/ api/tests/ -q

.PHONY: build-frontend
build-frontend: ## Type-check and build the frontend
	cd frontend && pnpm --filter rag-kit-web build

.PHONY: schema
schema: ## Regenerate the frontend API client from the OpenAPI schema
	$(COMPOSE) exec api uv run python manage.py spectacular --file /app/schema.yaml
	cd frontend && pnpm openapi:generate

# --------------------------------------------------------------- production ---

.PHONY: prod-up
prod-up: ## Build and start the production stack (needs .env.prod)
	@test -f .env.prod || (echo "missing .env.prod — cp .env.prod.template .env.prod and fill it in" && exit 1)
	$(COMPOSE_PROD) up -d --build
	@echo "serving on https://$$(grep -E '^DOMAIN=' .env.prod | cut -d= -f2)"

.PHONY: prod-down
prod-down: ## Stop the production stack (keeps volumes)
	$(COMPOSE_PROD) down

.PHONY: prod-logs
prod-logs: ## Tail production logs (S=service to filter)
	$(COMPOSE_PROD) logs -f $(S)

.PHONY: prod-superuser
prod-superuser: ## Create the first admin user in production
	$(COMPOSE_PROD) exec api uv run python manage.py createsuperuser

.PHONY: backup
backup: ## Back up production Postgres, Qdrant and MinIO
	./scripts/backup.sh

.PHONY: restore
restore: ## Restore a backup (B=backups/<timestamp>)
	@test -n "$(B)" || (echo "usage: make restore B=backups/<timestamp>" && exit 1)
	./scripts/restore.sh $(B)

# -------------------------------------------------------------------- extras ---

.PHONY: observability
observability: ## Start the stack with Langfuse tracing
	$(COMPOSE) --profile observability up -d
	@echo "langfuse  http://localhost:$${LANGFUSE_HOST_PORT:-3001}"

.PHONY: local-llm
local-llm: ## Start the stack with Ollama (fully offline)
	$(COMPOSE) --profile local-llm up -d
	$(COMPOSE) exec ollama ollama pull llama3.1
	$(COMPOSE) exec ollama ollama pull nomic-embed-text
	@echo "Set LLM_BASE_URL=http://ollama:11434/v1 and EMBEDDER_BASE_URL=http://ollama:11434/v1"
