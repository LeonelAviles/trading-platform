# Trading platform — developer entry points (PLATFORM-SPEC.md §5 Phase 0).
#
#   make dev      bare-metal: backend (uvicorn, :8123) + frontend (vite, :5173)
#   make up       docker compose up (backend, frontend)
#   make down     docker compose down
#   make ingest   scripts/ingest.py --all (raw .dbn.zst → data/market Parquet tiers)
#   make catalog  scripts/build_catalog.py (NautilusTrader ParquetDataCatalog)
#   make verify   scripts/verify_ingest.py (compare with the legacy mbo.duckdb)
#   make test     pytest (backend) + vitest (frontend, when present)
#   make lint     oxlint (frontend)

SHELL := /bin/bash
ROOT  := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON ?= python3.12
PY    := $(ROOT)/.venv/bin/python
PIP   := $(PY) -m pip
PG_COMPOSE := docker compose --env-file $(ROOT)/.postgres.env -p trading-features -f $(ROOT)/docker-compose.postgres.yml

.PHONY: dev backend frontend up down ingest catalog verify warm prepare-mbo-features knowledge-ml4t knowledge-ml4t-bootstrap test test-backend test-frontend lint venv
.PHONY: postgres-env postgres-up postgres-down postgres-shell postgres-status pgloader-image postgres-migrate

venv:
	@test -x $(PY) || $(PYTHON) -m venv $(ROOT)/.venv
	@$(PIP) install -q -r $(ROOT)/backend/requirements.txt

backend:
	cd $(ROOT)/backend && $(PY) -m uvicorn app:app --host 127.0.0.1 --port 8123 --reload --reload-exclude '.venv*/**'

frontend:
	cd $(ROOT)/frontend && npm run dev

# Runs both processes in the foreground; Ctrl-C stops both.
dev:
	@trap 'kill 0' INT TERM EXIT; \
	( cd $(ROOT)/backend && $(PY) -m uvicorn app:app --host 127.0.0.1 --port 8123 --reload --reload-exclude '.venv*/**' ) & \
	( cd $(ROOT)/frontend && npm run dev ) & \
	wait

up:
	docker compose up --build

down:
	docker compose down

postgres-env:
	@$(PY) -c 'import os, secrets; from pathlib import Path; p = Path("$(ROOT)/.postgres.env"); p.exists() or os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w").write("POSTGRES_USER=trading\nPOSTGRES_DB=trading_features\nPOSTGRES_PORT=5432\nPOSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n")'

postgres-up: postgres-env
	$(PG_COMPOSE) up -d --wait

postgres-down:
	$(PG_COMPOSE) down

postgres-shell:
	$(PG_COMPOSE) exec postgres sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

postgres-status:
	$(PG_COMPOSE) ps

pgloader-image:
	docker build -f $(ROOT)/backend/migration/Dockerfile.pgloader -t trading-platform-pgloader:local $(ROOT)

postgres-migrate:
	@test -n "$(DB)" || (echo 'Usage: make postgres-migrate DB=empty_destination_database'; exit 1)
	cd $(ROOT)/backend && $(PY) scripts/migrate_to_postgres.py --database "$(DB)"

ingest:
	cd $(ROOT)/backend && $(PY) scripts/ingest.py --all

catalog:
	cd $(ROOT)/backend && $(PY) scripts/build_catalog.py

verify:
	cd $(ROOT)/backend && $(PY) scripts/verify_ingest.py

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

warm:
	cd $(ROOT)/backend && $(PY) scripts/warm_replay.py $(ROOT_SYMBOL) $(DATE)

prepare-mbo-features:
	@test -n "$(REQUEST)" || (echo 'Usage: make prepare-mbo-features REQUEST=/path/to/request.json [RESTORE=1]'; exit 1)
	cd $(ROOT)/backend && $(PY) scripts/prepare_mbo_features.py --request "$(REQUEST)" $(if $(filter 1,$(RESTORE)),--restore,)

knowledge-ml4t:
	cd $(ROOT)/backend && $(PY) scripts/ingest_knowledge.py $(ROOT)/data/knowledge/repos/ml4t \
		--name machine-learning-for-trading \
		--url https://github.com/stefan-jansen/machine-learning-for-trading \
		--license MIT

knowledge-ml4t-bootstrap:
	cd $(ROOT)/backend && $(PY) scripts/bootstrap_ml4t_knowledge.py
