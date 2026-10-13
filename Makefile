.DEFAULT_GOAL := help
.PHONY: help install up up-real down logs n8n-owner test test-n8n lint format workflows check synth eval-offline eval-real smoke mcp web send-email summary

FREE_MODEL ?= nvidia/nemotron-3-super-120b-a12b:free
FREE_FALLBACKS ?= nvidia/nemotron-3-ultra-550b-a55b:free
N8N_OWNER_EMAIL ?= owner@relay.localhost
N8N_OWNER_PASSWORD ?= Relay-local-demo-1

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Python dependencies (uv) and the dashboard's (npm)
	uv sync
	cd web && npm ci --no-audit --no-fund

up:  ## Whole stack with the offline model: n8n, Relay API, mocks, Mailpit, PostgreSQL, dashboard
	docker compose --profile web up --build -d
	@echo "dashboard http://localhost:3000 | n8n http://localhost:5678 | API http://localhost:8100/docs | Mailpit http://localhost:8025"

up-real:  ## Same, with a free OpenRouter model (needs OPENROUTER_API_KEY in the environment)
	RELAY_LLM_PROVIDER=openrouter RELAY_LLM_MODEL=$(FREE_MODEL) RELAY_LLM_FALLBACK_MODELS=$(FREE_FALLBACKS) \
	  docker compose --profile web up --build -d

down:  ## Stop the stack (data volumes are kept; `docker compose down -v` deletes them)
	docker compose --profile web down

logs:  ## Follow the n8n and Relay logs
	docker compose logs -f n8n relay

n8n-owner:  ## Create the local n8n owner account (local demo credentials; change them for anything real)
	curl -fsS -X POST http://localhost:5678/rest/owner/setup -H 'content-type: application/json' \
	  -d '{"email":"$(N8N_OWNER_EMAIL)","firstName":"Relay","lastName":"Owner","password":"$(N8N_OWNER_PASSWORD)"}' >/dev/null
	@echo "n8n owner created: $(N8N_OWNER_EMAIL) (password from N8N_OWNER_PASSWORD in the Makefile / .env.example)"

test:  ## Unit tests (no keys, no network, no Docker)
	uv run pytest

test-n8n:  ## Integration tests through the running n8n (after `make up`)
	RELAY_N8N_URL=http://localhost:5678 uv run pytest tests/test_integration_n8n.py -v

lint:  ## Ruff, format check, mypy strict, workflow JSON validation
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy
	uv run relay workflows check

format:  ## Auto-format and fix lint issues
	uv run ruff format src tests
	uv run ruff check --fix src tests

workflows:  ## Regenerate n8n/workflows/*.json from relay/workflows/builder.py (then restart: docker compose up -d --force-recreate n8n-import n8n)
	uv run relay workflows build

check:  ## Validate the workflow JSON against the n8n node catalog
	uv run relay workflows check

synth:  ## Regenerate the synthetic datasets (deterministic)
	uv run relay synth

eval-offline:  ## Every suite through n8n with the offline rules model (after `make up`)
	uv run relay eval all --name offline

eval-real:  ## Every suite through n8n with the free model (after `make up-real`)
	uv run relay eval all --name free

smoke:  ## One call per task for a few free models (needs OPENROUTER_API_KEY)
	uv run relay smoke-models $(FREE_MODEL) $(FREE_FALLBACKS)

mcp:  ## MCP server over stdio for Claude Code / Claude Desktop
	uv run relay mcp

web:  ## Dashboard dev server on http://localhost:3000
	cd web && npm run dev

send-email:  ## Send a synthetic email through Mailpit SMTP to the triage workflow (EMAIL=em-025)
	uv run relay send-email $${EMAIL:-em-025}

summary:  ## Rebuild results/summary.json and print the headline numbers
	uv run relay summary
