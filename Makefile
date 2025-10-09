# Makefile for dfir-automation-analysis

# ---- Config ----
COMPOSE ?= docker compose
DOCKER  ?= docker
BUILDX  ?= docker buildx

DATA_DB     := data/db
DATA_MEDIA  := data/media

# ---- Help ----
.PHONY: help
help:
	@echo "Usage: make <target>"
	@echo
	@echo "Main:"
	@echo "  up             Create data dirs + docker compose up -d --build"
	@echo "  up-all         Same as up but includes the 'tools' profile (build parsers)"
	@echo "  build          docker compose build"
	@echo "  build-parsers  Build ez-parsers:latest via compose (uses profile tools)"
	@echo "  build-parsers-amd64  Build ez-parsers:latest with linux/amd64 (Mac/ARM)"
	@echo
	@echo "Clean & Reset:"
	@echo "  clean-data     Remove $(DATA_DB) and $(DATA_MEDIA), then recreate"
	@echo "  prune          compose down -v + docker system prune -af --volumes"
	@echo "  reset          Down + prune + clean-data"
	@echo "  rebuild        reset + up-all"
	@echo
	@echo "Dev Utils:"
	@echo "  down           docker compose down"
	@echo "  down-v         docker compose down -v --remove-orphans"
	@echo "  logs           docker compose logs -f --tail=200"
	@echo "  ps             docker compose ps"
	@echo "  status         Show ez-parsers image status"
	@echo "  shell-django   Open bash inside django container"
	@echo "  migrate        Run Django migrations"
	@echo "  makemigrations Run makemigrations for app 'api'"
	@echo "  collectstatic  Run Django collectstatic --noinput"

# ---- Helpers ----
.PHONY: prepare-dirs
prepare-dirs:
	@mkdir -p $(DATA_DB) $(DATA_MEDIA)

# ---- Build & Up ----
.PHONY: up
up: prepare-dirs
	$(COMPOSE) up -d --build

# รวมโปรไฟล์ tools (ถ้า parsers ถูกตั้ง profiles: ["tools"])
.PHONY: up-all
up-all: prepare-dirs
	$(COMPOSE) --profile tools up -d --build

.PHONY: build
build:
	$(COMPOSE) build

.PHONY: build-parsers
build-parsers:
	$(COMPOSE) --profile tools build parsers

# ใช้ตอน Mac/Apple Silicon งอแงเรื่องแพลตฟอร์ม
.PHONY: build-parsers-amd64
build-parsers-amd64:
	$(BUILDX) build --platform=linux/amd64 -f parsers/Dockerfile.parsers -t ez-parsers:latest parsers --load

# ---- Clean / Reset ----
.PHONY: clean-data
clean-data:
	@echo "Removing $(DATA_DB) and $(DATA_MEDIA)..."
	@rm -rf $(DATA_DB) $(DATA_MEDIA)
	@mkdir -p $(DATA_DB) $(DATA_MEDIA)

.PHONY: prune
prune:
	-$(COMPOSE) down -v --remove-orphans
	$(DOCKER) system prune -af --volumes
	@echo "Pruned Docker. Also cleaning data dirs..."
	@rm -rf $(DATA_DB) $(DATA_MEDIA)
	@mkdir -p $(DATA_DB) $(DATA_MEDIA)

.PHONY: reset
reset: prune clean-data

.PHONY: rebuild
rebuild: reset up-all

# ---- Dev Utils ----
.PHONY: down
down:
	$(COMPOSE) down

.PHONY: down-v
down-v:
	$(COMPOSE) down -v --remove-orphans

.PHONY: logs
logs:
	$(COMPOSE) logs -f --tail=200

.PHONY: ps
ps:
	$(COMPOSE) ps

.PHONY: status
status:
	-$(DOCKER) image inspect ez-parsers:latest --format 'ez-parsers: {{.Id}}'
	@echo "(If not found, run: 'make build-parsers' or 'make up-all')"

.PHONY: shell-django
shell-django:
	$(COMPOSE) exec django bash

.PHONY: migrate
migrate:
	$(COMPOSE) exec django python manage.py migrate

.PHONY: makemigrations
makemigrations:
	$(COMPOSE) exec django python manage.py makemigrations api

.PHONY: collectstatic
collectstatic:
	$(COMPOSE) exec django python manage.py collectstatic --noinput

# ---- Cache Cleanup ----
.PHONY: clean-pyc clear-cache clear-all-caches

# ลบไฟล์แคชของ Python (.pyc/.pyo) และโฟลเดอร์ __pycache__, .pytest_cache, .mypy_cache
clean-pyc:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + ; \
	find . -name '*.py[co]' -type f -delete ; \
	find . -name '.pytest_cache' -type d -prune -exec rm -rf {} + ; \
	find . -name '.mypy_cache' -type d -prune -exec rm -rf {} +

# สั่งล้าง Django cache ภายในคอนเทนเนอร์ (ต้องมี service ชื่อ django กำลังรัน)
clear-cache:
	$(COMPOSE) exec django python manage.py shell -c "from django.core.cache import cache; cache.clear(); print('Django cache cleared')"

# สะดวกกดทีเดียวล้างหมด
clear-all-caches: clean-pyc clear-cache
