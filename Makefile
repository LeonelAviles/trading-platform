# Trading platform — developer entry points (PLATFORM-SPEC.md §5 Phase 0).
#
#   make dev      bare-metal: backend (uvicorn, :8123) + frontend (vite, :5173)
#   make up       docker compose up (backend, frontend, neo4j)
#   make down     docker compose down
#   make ingest   scripts/ingest.py --all (Phase 1; legacy ingest until then)
#   make test     pytest (backend) + vitest (frontend, when present)
#   make lint     oxlint (frontend)

SHELL := /bin/bash
ROOT  := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PY    := $(ROOT)/backend/.venv/bin/python
PIP   := $(ROOT)/backend/.venv/bin/pip

.PHONY: dev backend frontend up down ingest test test-backend test-frontend lint venv

venv:
	@test -x $(PY) || python3 -m venv $(ROOT)/backend/.venv
	@$(PIP) install -q -r $(ROOT)/backend/requirements.txt

backend:
	cd $(ROOT)/backend && $(PY) -m uvicorn app:app --host 127.0.0.1 --port 8123 --reload

frontend:
	cd $(ROOT)/frontend && npm run dev

# Runs both processes in the foreground; Ctrl-C stops both.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	( cd $(ROOT)/backend && $(PY) -m uvicorn app:app --host 127.0.0.1 --port 8123 --reload ) & \
	( cd $(ROOT)/frontend && npm run dev ) & \
	wait

up:
	docker compose up --build

down:
	docker compose down

ingest:
	@if [ -f $(ROOT)/backend/scripts/ingest.py ]; then \
		cd $(ROOT)/backend && $(PY) scripts/ingest.py --all; \
	else \
		echo "scripts/ingest.py arrives in Phase 1; using legacy ingest_dbn_to_duckdb.py"; \
		cd $(ROOT)/backend && $(PY) scripts/ingest_dbn_to_duckdb.py --all; \
	fi

test: test-backend test-frontend

test-backend:
	cd $(ROOT)/backend && $(PY) -m pytest -q

test-frontend:
	@cd $(ROOT)/frontend && if node -e "process.exit(require('./package.json').scripts.test ? 0 : 1)"; then \
		npm test -- --run; \
	else \
		echo "no frontend tests yet (vitest arrives with the first pure helper in Phase 3)"; \
	fi

lint:
	cd $(ROOT)/frontend && npm run lint
