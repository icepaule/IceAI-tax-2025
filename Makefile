SHELL := /bin/bash

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

status:
	docker compose up -d status-web

status-logs:
	docker compose logs -f --tail=100 status-web

screenshot-status:
	./scripts/capture_status_screenshot.sh

docs-list:
	ls -1 docs/*.md

portal-up:
	docker compose up -d portal-browser portal-fetcher

portal-logs:
	docker compose logs -f --tail=100 portal-browser portal-fetcher

portal-login-amazon:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal amazon --interactive"

portal-login-ebay:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal ebay --interactive"

portal-login-vodafone:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal vodafone --interactive"

portal-fetch-amazon:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal amazon --year 2025"

portal-fetch-ebay:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal ebay --year 2025"

portal-fetch-vodafone:
	docker compose run --rm portal-fetcher sh -c "pip install --no-cache-dir -r requirements_portal.txt && python portal_fetcher.py --portal vodafone --year 2025"

bootstrap:
	cp -n .env.example .env || true
	mkdir -p data/{inbox,processed,exports,logs,reference,paperless/{data,media,export},postgres}
	docker compose build tax-pipeline

dry-run:
	docker compose run --rm tax-pipeline python main.py --mode dry-run

run:
	docker compose run --rm tax-pipeline python main.py --mode run

run-current:
	docker compose run --rm tax-pipeline python main.py --mode run --current-year

run-year:
	docker compose run --rm tax-pipeline python main.py --mode run --tax-year $(YEAR)

collect:
	docker compose run --rm tax-pipeline python main.py --mode collect

collect-current:
	docker compose run --rm tax-pipeline python main.py --mode collect --current-year

collect-year:
	docker compose run --rm tax-pipeline python main.py --mode collect --tax-year $(YEAR)

compare:
	docker compose run --rm tax-pipeline python main.py --mode compare

migrate-ecodms-dry:
	docker compose run --rm tax-pipeline python paperless_importer.py --dry-run

migrate-ecodms:
	docker compose run --rm tax-pipeline python paperless_importer.py

ecodms-manifest:
	python3 app/ecodms_offline_importer.py \
		--backup-sql data/ecodms-export/raw/backup.sql \
		--export-zip data/ecodms-export/raw/workdir/exports/f436f12b-2033-498f-9a1d-758bc777d8d6.zip \
		--manifest data/ecodms-export/ecodms_paperless_manifest.csv \
		--manifest-only

migrate-ecodms-offline-dry:
	python3 app/ecodms_offline_importer.py \
		--backup-sql data/ecodms-export/raw/backup.sql \
		--export-zip data/ecodms-export/raw/workdir/exports/f436f12b-2033-498f-9a1d-758bc777d8d6.zip \
		--manifest data/ecodms-export/ecodms_paperless_manifest.csv \
		--dry-run

migrate-ecodms-offline:
	python3 app/ecodms_offline_importer.py \
		--backup-sql data/ecodms-export/raw/backup.sql \
		--export-zip data/ecodms-export/raw/workdir/exports/f436f12b-2033-498f-9a1d-758bc777d8d6.zip \
		--manifest data/ecodms-export/ecodms_paperless_manifest.csv

prepare-ecodms-backup:
	./scripts/prepare_ecodms_backup.sh

supplier-gap:
	docker compose run --rm tax-pipeline python supplier_gap_report.py

supplier-gap-current:
	docker compose run --rm tax-pipeline python supplier_gap_report.py --current-year

supplier-gap-year:
	docker compose run --rm tax-pipeline python supplier_gap_report.py --tax-year $(YEAR)

scan-up:
	docker compose up -d scan-mover paperless-ai storage-path-sync

scan-down:
	docker compose stop scan-mover paperless-ai storage-path-sync

scan-logs:
	docker compose logs -f --tail=100 scan-mover

ai-logs:
	docker compose logs -f --tail=100 paperless-ai

ai-ui:
	@echo "paperless-ai UI: http://localhost:3100"

setup-tags:
	docker compose run --rm tax-pipeline python setup_paperless_tags.py

sync-storage-paths:
	docker compose run --rm tax-pipeline python paperless_storage_path_sync.py

sync-storage-paths-dry:
	docker compose run --rm tax-pipeline python paperless_storage_path_sync.py --dry-run

# Ingest manually downloaded PDFs (e.g. goldgas/sipgate/strato portals) placed into /srv/taxdrop.
# This avoids IMAP/portal collection and only processes the staged files.
ingest-drop-year:
	docker compose exec -T tax-pipeline bash -lc 'set -euo pipefail; ts=$$(date +%Y%m%d_%H%M%S); inbox=/data/pipeline_inbox_drop_'"$${ts}"'; mkdir -p "$$inbox"; for p in /manual-drop/*.pdf /manual-drop/*.png /manual-drop/*.jpg /manual-drop/*.jpeg; do [ -f "$$p" ] && cp -n "$$p" "$$inbox"/ || true; done; export PIPELINE_INBOX_DIR="$$inbox"; export PROCESS_TEXT_DOCS=false; export STEUERBOX_ENABLED=false; python -u main.py --mode process --tax-year $(YEAR); export EXPORT_CSV=/data/exports/steuer_$(YEAR)_export.csv; export TAX_YEAR=$(YEAR); export IMPORT_TXT=true; python -u paperless_import_from_export.py; python -u paperless_tax_tag_sync.py; python -u supplier_gap_report.py --tax-year $(YEAR)'
