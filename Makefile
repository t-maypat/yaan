# Thin wrappers for Linux/macOS (and judges). Each recipe is the exact command to run
# by hand where make isn't installed (the Windows dev box): see the README Quickstart.
RUN      := uv run --project backend
GRAPH    ?= lore_herb
PRODUCTS ?=
SPLIT    ?= dev

.PHONY: help setup up down logs schema data ingest health eval seed test test-unit lint api

help:
	@echo "targets: setup up down logs schema data ingest health eval seed test test-unit lint api"

# Python env from backend/uv.lock
setup:
	uv sync --project backend

# FalkorDB + Browser (http://localhost:3000)
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f falkordb

schema:
	$(RUN) yaan schema --graph $(GRAPH)

data:
	$(RUN) yaan fetch-herb

ingest:
	$(RUN) yaan ingest --graph $(GRAPH) --products "$(PRODUCTS)"

health:
	$(RUN) yaan health --graph $(GRAPH)

eval:
	$(RUN) yaan eval --split $(SPLIT)

seed:
	$(RUN) yaan seed --graph $(GRAPH)

test:
	$(RUN) pytest backend

test-unit:
	$(RUN) pytest backend -m "not db"

lint:
	$(RUN) ruff check backend

api:
	$(RUN) uvicorn app.main:app --reload --port 8000
