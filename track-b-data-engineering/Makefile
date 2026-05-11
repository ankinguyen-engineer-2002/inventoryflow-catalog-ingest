.PHONY: help up down bootstrap track-b-batch track-b-stream track-b-query dagster-dev test clean

help:
	@echo "Track B targets:"
	@echo ""
	@echo "  make up               Bring up the full stack (MinIO, Iceberg REST, PG, Redpanda, RisingWave)"
	@echo "  make down             Tear down the stack (preserves volumes)"
	@echo "  make bootstrap        Install poetry deps + create Iceberg namespace"
	@echo "  make track-b-batch    Run bronze + silver + gold asset materialisation"
	@echo "  make track-b-stream   Apply RisingWave views + seed Redpanda"
	@echo "  make track-b-query    Run DuckDB-on-Iceberg analytical query demo"
	@echo "  make dagster-dev      Start Dagster webserver locally on :3000"
	@echo "  make test             Run pytest suite"
	@echo "  make clean            Remove generated artefacts (target/, .dagster/)"

up:
	docker-compose up -d
	@echo ""
	@echo "Waiting for services to become healthy..."
	@sleep 5
	@docker-compose ps

down:
	docker-compose down

bootstrap:
	poetry install
	poetry run dagster asset materialize -m dagster_project.definitions --select bronze_catalog_rows || true

track-b-batch:
	poetry run dagster asset materialize -m dagster_project.definitions \
	  --select '*gold_products_mart'

track-b-stream:
	@echo "Applying RisingWave streaming views..."
	psql -h localhost -p 4566 -U root -d dev -f streaming/risingwave_views.sql
	@echo ""
	@echo "Seeding 10 events into Redpanda..."
	poetry run python streaming/redpanda_seed.py --count 10
	@echo ""
	@echo "Verify with: psql -h localhost -p 4566 -U root -d dev -c 'SELECT count(*) FROM mv_live_inventory;'"

track-b-query:
	poetry run python notebooks/duckdb_iceberg_demo.py

dagster-dev:
	poetry run dagster dev -m dagster_project.definitions

test:
	poetry run pytest tests/ -v

clean:
	rm -rf target/ .dagster/ dbt_packages/ logs/
