.PHONY: help install migrate run workers backfill clean test

help:
	@echo "Slack Intelligence System - Make Commands"
	@echo ""
	@echo "install      - Install dependencies"
	@echo "migrate      - Run database migrations"
	@echo "run          - Run FastAPI application"
	@echo "workers      - Run all consumer workers"
	@echo "worker-event - Run event consumer only"
	@echo "worker-embed - Run embedding consumer only"
	@echo "worker-proc  - Run processing consumer only"
	@echo "backfill     - Run backfill worker"
	@echo "clean        - Clean Python cache files"
	@echo "test         - Run tests"

install:
	pip install -r requirements.txt

migrate:
	alembic revision --autogenerate -m "Auto migration"
	alembic upgrade head

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

workers:
	python -m app.workers.run_consumers

worker-event:
	python -m app.workers.event_main

worker-embed:
	python -m app.workers.embedding_consumer

worker-proc:
	python -m app.workers.processing_main

backfill:
	python -m app.workers.backfill_worker

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete

test:
	pytest tests/
