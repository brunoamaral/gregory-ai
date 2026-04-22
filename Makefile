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

.PHONY: help build push db-pull db-restore

help:
	@echo "Available targets:"
	@echo "  build       Build the Docker image (tagged :latest and :TAG)"
	@echo "  push        Build and push a multi-arch image to Docker Hub"
	@echo "  db-pull     Dump the production database and restore it locally"
	@echo "  db-restore  Restore the most recent backup in $(BACKUP_DIR)/"

## Build the Gregory Docker image.
## Override image name or tag: make build IMAGE=myrepo/myimage TAG=v1.0
build:
	DOCKER_BUILDKIT=1 docker build -t $(IMAGE):$(TAG) -t $(IMAGE):latest -f Dockerfile .
	@echo "==> Built $(IMAGE):$(TAG) and $(IMAGE):latest"

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

$(BACKUP_DIR):
	mkdir -p $(BACKUP_DIR)
