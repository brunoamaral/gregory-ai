# GregoryAI Makefile
# Load variables from .env if present, stripping surrounding quotes
-include .env
export

# Strip surrounding single/double quotes from variables read from .env
POSTGRES_USER     := $(patsubst "%",%,$(patsubst '%',%,$(POSTGRES_USER)))
POSTGRES_PASSWORD := $(patsubst "%",%,$(patsubst '%',%,$(POSTGRES_PASSWORD)))
POSTGRES_DB       := $(patsubst "%",%,$(patsubst '%',%,$(POSTGRES_DB)))

PROD_HOST ?= House
PROD_SSH_USER ?= gregory
BACKUP_DIR := backups
DUMP_FILE := $(BACKUP_DIR)/db_pull_$(shell date +%Y%m%d_%H%M%S).sql

# Docker image settings
IMAGE ?= amaralbruno/gregory-ai
TAG   ?= $(shell git rev-parse --short HEAD)
PLATFORMS ?= linux/amd64,linux/arm64
BUILDER ?= gregory-multiarch

.PHONY: help build push db-pull db-restore db-upgrade db-upgrade-finish ml-export ml-train ml-ship

help:
	@echo "Available targets:"
	@echo "  build              Build the Docker image (tagged :latest and :TAG)"
	@echo "  check              runs `uvx ruff check django/` to check for errors in the code"
	@echo "  push               Build and push a multi-arch image to Docker Hub"
	@echo "  db-pull            Dump the production database and restore it locally"
	@echo "  db-restore         Restore the most recent backup in $(BACKUP_DIR)/"
	@echo "  db-upgrade         Step 1 of major-version upgrade: dump current DB, stop db, move data dir aside"
	@echo "  db-upgrade-finish  Step 2: recreate db container from new image, restore dump, migrate"
	@echo "  ml-export          Export training data on production and copy the CSV here"
	@echo "  ml-train           Train ML_ALGO locally from the newest exported dataset"
	@echo "  ml-ship            Copy the newest trained model version to production and verify"
	@echo ""
	@echo "Off-box ML training (defaults: ML_TEAM=$(ML_TEAM) ML_SUBJECT=$(ML_SUBJECT) ML_ALGO=$(ML_ALGO)):"
	@echo "  make ml-export && make ml-train && make ml-ship"

## Build the Gregory Docker image.
## Override image name or tag: make build IMAGE=myrepo/myimage TAG=v1.0
build:
	DOCKER_BUILDKIT=1 docker build -t $(IMAGE):$(TAG) -t $(IMAGE):latest -f Dockerfile .
	@echo "==> Built $(IMAGE):$(TAG) and $(IMAGE):latest"

## Check the Django code for errors using uvx.
check:
	@echo "Running code checks with ruff..."
	@uvx ruff check django/

## Push a multi-arch image manifest to Docker Hub.
## Override platforms if needed: make push PLATFORMS=linux/amd64
push:
	@if docker buildx inspect $(BUILDER) >/dev/null 2>&1; then \
		if [ "$$(docker buildx inspect $(BUILDER) --format '{{.Driver}}')" != "docker-container" ]; then \
			docker buildx rm $(BUILDER) >/dev/null; \
			docker buildx create --name $(BUILDER) --driver docker-container --use >/dev/null; \
		fi \
	else \
		docker buildx create --name $(BUILDER) --driver docker-container --use >/dev/null; \
	fi
	@docker buildx use $(BUILDER)
	@docker buildx inspect --bootstrap >/dev/null
	docker buildx build --builder $(BUILDER) --platform $(PLATFORMS) -t $(IMAGE):$(TAG) -t $(IMAGE):latest -f Dockerfile . --push
	@echo "==> Pushed multi-arch $(IMAGE):$(TAG) and $(IMAGE):latest for $(PLATFORMS)"

## Fetch the production DB and restore it into the local Docker postgres container.
##
## Requirements on the production host:
##   - pg_dump accessible (postgres package installed)
##   - SSH access as $(PROD_SSH_USER)@$(PROD_HOST)
##   - The same POSTGRES_* env vars apply on the remote host
##
## Override prod credentials if they differ from local, e.g.:
##   make db-pull PROD_POSTGRES_USER=myuser PROD_POSTGRES_DB=mydb
PROD_POSTGRES_USER ?= $(POSTGRES_USER)
PROD_POSTGRES_DB   ?= $(POSTGRES_DB)

db-pull: | $(BACKUP_DIR)
	@echo "==> Dumping production database from $(PROD_HOST) ..."
	ssh $(PROD_SSH_USER)@$(PROD_HOST) \
		"docker exec db pg_dump -U $(PROD_POSTGRES_USER) -d $(PROD_POSTGRES_DB) --no-owner --no-privileges -F p" \
		> $(DUMP_FILE)
	@echo "==> Dump saved to $(DUMP_FILE)"
	@echo "==> Waiting for local postgres to be ready ..."
	@until docker exec db pg_isready -U $(POSTGRES_USER) -q; do sleep 1; done
	@echo "==> Dropping and recreating local database $(POSTGRES_DB) ..."
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "DROP DATABASE IF EXISTS \"$(POSTGRES_DB)\";"
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "CREATE DATABASE \"$(POSTGRES_DB)\";"
	@echo "==> Restoring dump into local database ..."
	docker exec -i db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) < $(DUMP_FILE)
	@echo "==> Running Django migrations ..."
	docker exec gregory python manage.py migrate --run-syncdb
	docker exec gregory python manage.py createcachetable gregory_cache
	@echo "==> Done. Local database is now a copy of production."

## Restore the most recently modified SQL file in $(BACKUP_DIR)/
db-restore: | $(BACKUP_DIR)
	$(eval LATEST := $(shell ls -t $(BACKUP_DIR)/*.sql 2>/dev/null | head -1))
	@if [ -z "$(LATEST)" ]; then echo "No backup files found in $(BACKUP_DIR)/"; exit 1; fi
	@echo "==> Restoring $(LATEST) ..."
	@until docker exec db pg_isready -U $(POSTGRES_USER) -q; do sleep 1; done
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "DROP DATABASE IF EXISTS \"$(POSTGRES_DB)\";"
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "CREATE DATABASE \"$(POSTGRES_DB)\";"
	docker exec -i db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) < $(LATEST)
	docker exec gregory python manage.py migrate --run-syncdb
	@echo "==> Done."

## Major-version Postgres upgrade (two-step for rollback safety).
##
## Pre-condition: docker-compose.yaml's `db` image tag already points at the NEW major version.
## The currently running `db` container can still be on the OLD version — that is exactly what
## step 1 expects.
##
## Workflow:
##   1. make db-upgrade         # dumps current DB -> removes db container -> renames postgres-data
##   2. make db-upgrade-finish  # recreates db container from the new image, restores dump, migrates
##
## Rollback before step 2: revert docker-compose.yaml's image tag,
##   `mv postgres-data.pre-upgrade.* postgres-data`, then `docker compose up -d db`.
UPGRADE_TIMESTAMP := $(shell date +%Y%m%d_%H%M%S)
UPGRADE_DUMP      := $(BACKUP_DIR)/pre_upgrade_$(UPGRADE_TIMESTAMP).sql

db-upgrade: | $(BACKUP_DIR)
	@if [ ! -d "./postgres-data" ]; then \
		echo "ERROR: ./postgres-data does not exist. Run this target from the repo root after starting the db container at least once."; \
		exit 1; \
	fi
	@echo "==> Dumping current DB to $(UPGRADE_DUMP)"
	docker exec db pg_dump -U $(POSTGRES_USER) -d $(POSTGRES_DB) \
		--no-owner --no-privileges -F p > $(UPGRADE_DUMP)
	@echo "==> Removing db container (data dir on host is untouched)"
	docker compose rm -sf db
	@echo "==> Moving ./postgres-data aside (rollback safety)"
	mv ./postgres-data ./postgres-data.pre-upgrade.$(UPGRADE_TIMESTAMP)
	@echo "==> NEXT: run: make db-upgrade-finish"

db-upgrade-finish: | $(BACKUP_DIR)
	@echo "==> Starting db container from new image (empty data dir)"
	docker compose up -d db
	@until docker exec db pg_isready -U $(POSTGRES_USER) -q; do sleep 1; done
	$(eval DUMP := $(shell ls -t $(BACKUP_DIR)/pre_upgrade_*.sql 2>/dev/null | head -1))
	@if [ -z "$(DUMP)" ]; then echo "No pre_upgrade_*.sql found in $(BACKUP_DIR)/"; exit 1; fi
	@echo "==> Restoring $(DUMP)"
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "DROP DATABASE IF EXISTS \"$(POSTGRES_DB)\";"
	docker exec db psql -U $(POSTGRES_USER) -d postgres -c "CREATE DATABASE \"$(POSTGRES_DB)\";"
	docker exec -i db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) < $(DUMP)
	@echo "==> Ensuring gregory app container is running"
	docker compose up -d gregory
	@echo "==> Running Django migrations"
	docker exec gregory python manage.py migrate --run-syncdb
	docker exec gregory python manage.py createcachetable gregory_cache
	@echo "==> Done. Verify, then remove postgres-data.pre-upgrade.* once happy."

$(BACKUP_DIR):
	mkdir -p $(BACKUP_DIR)

## Off-box ML training: export the dataset on production, train here, ship the
## artifacts back. Prediction on production picks up the new version dir
## automatically (resolve_model_version), no restart needed.
##
## Requirements:
##   - ml-export: the production compose file mounts ./django/datasets:/code/datasets
##   - ml-train: local containers running (docker compose up -d) and the team/subject
##     present in the local DB (make db-pull if missing)
##   - ml-ship: rsync on both ends
##
## Override scope per run, e.g.:
##   make ml-export ml-train ml-ship ML_ALGO=lstm
##   make ml-train ML_TEAM=other-team ML_SUBJECT=other-subject ML_ALGO=lgbm_tfidf
ML_TEAM      ?= team-gregory
ML_SUBJECT   ?= multiple-sclerosis
ML_ALGO      ?= pubmed_bert
ML_VERSION   ?=
PROD_APP_DIR ?= gregory-ai
DATASETS_DIR := django/datasets
ML_MODEL_PATH = $(ML_TEAM)/$(ML_SUBJECT)/$(ML_ALGO)

ml-export:
	@echo "==> Exporting training data for $(ML_TEAM)/$(ML_SUBJECT) on $(PROD_HOST) ..."
	ssh $(PROD_SSH_USER)@$(PROD_HOST) \
		"docker exec gregory python manage.py export_training_data --team $(ML_TEAM) --subject $(ML_SUBJECT) --all-articles"
	@mkdir -p $(DATASETS_DIR)
	scp "$(PROD_SSH_USER)@$(PROD_HOST):$(PROD_APP_DIR)/django/datasets/$(ML_TEAM)_$(ML_SUBJECT)_*.csv" $(DATASETS_DIR)/
	@echo "==> Dataset copied to $(DATASETS_DIR)/ — next: make ml-train"

ml-train:
	$(eval DATASET := $(shell ls -t $(DATASETS_DIR)/$(ML_TEAM)_$(ML_SUBJECT)_*.csv 2>/dev/null | head -1))
	@if [ -z "$(DATASET)" ]; then \
		echo "ERROR: no dataset for $(ML_TEAM)/$(ML_SUBJECT) in $(DATASETS_DIR)/. Run: make ml-export"; \
		exit 1; \
	fi
	@echo "==> Training $(ML_ALGO) for $(ML_TEAM)/$(ML_SUBJECT) from $(DATASET) ..."
	docker exec gregory python manage.py train_models \
		--team $(ML_TEAM) --subject $(ML_SUBJECT) --algo $(ML_ALGO) \
		--dataset-file /code/datasets/$(notdir $(DATASET)) --verbose 3
	@echo "==> Artifacts in django/models/$(ML_MODEL_PATH)/ — next: make ml-ship"

## Ships the newest version dir by default; pin one with ML_VERSION=YYYYMMDD[_n]
ml-ship:
	$(eval ML_VERSION := $(or $(ML_VERSION),$(shell ls -t django/models/$(ML_MODEL_PATH)/ 2>/dev/null | head -1)))
	@if [ -z "$(ML_VERSION)" ]; then \
		echo "ERROR: no trained versions in django/models/$(ML_MODEL_PATH)/. Run: make ml-train"; \
		exit 1; \
	fi
	@echo "==> Shipping $(ML_MODEL_PATH)/$(ML_VERSION) to $(PROD_HOST) ..."
	ssh $(PROD_SSH_USER)@$(PROD_HOST) "mkdir -p $(PROD_APP_DIR)/django/models/$(ML_MODEL_PATH)/$(ML_VERSION)"
	rsync -av django/models/$(ML_MODEL_PATH)/$(ML_VERSION)/ \
		$(PROD_SSH_USER)@$(PROD_HOST):$(PROD_APP_DIR)/django/models/$(ML_MODEL_PATH)/$(ML_VERSION)/
	@echo "==> Verifying with a dry-run prediction on $(PROD_HOST) ..."
	ssh $(PROD_SSH_USER)@$(PROD_HOST) \
		"docker exec gregory python manage.py predict_articles --team $(ML_TEAM) --subject $(ML_SUBJECT) --algo $(ML_ALGO) --model-version $(ML_VERSION) --dry-run --verbose 1"
	@echo "==> Done. $(ML_ALGO) $(ML_VERSION) is live for $(ML_TEAM)/$(ML_SUBJECT)."
