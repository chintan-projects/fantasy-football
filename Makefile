.DEFAULT_GOAL := help
SHELL := /bin/bash

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

setup: ## Install backend and frontend dependencies
	cd backend && uv sync --extra dev
	cd frontend && npm install

dev: ## Run backend and frontend together
	(cd backend && uv run uvicorn ff.api.app:app --reload --port 8000) & \
	(cd frontend && npm run dev); wait

test: ## Run the test suite
	cd backend && uv run pytest

check: ## Lint, format check and type check everything
	cd backend && uv run ruff check src tests && uv run ruff format --check src tests
	cd backend && uv run mypy src/ff/domain src/ff/services
	cd frontend && npm run lint

fix: ## Auto-format and auto-fix
	cd backend && uv run ruff check --fix src tests && uv run ruff format src tests

auth: ## Authorize with Yahoo (opens a browser, writes .tokens/yahoo.json)
	cd backend && uv run python ../scripts/yahoo_auth.py

leagues: ## List your Yahoo leagues and their keys
	cd backend && uv run python ../scripts/list_leagues.py

probe: ## Test whether Yahoo write access actually works
	cd backend && uv run python ../scripts/probe_write.py

.PHONY: help setup dev test check fix auth leagues probe
