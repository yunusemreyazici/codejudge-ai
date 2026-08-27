.PHONY: database-migrate database-test postgres-up sandbox-build sandbox-test

postgres-up:
	docker compose up -d postgres

database-migrate:
	uv run alembic upgrade head

database-test:
	uv run pytest -v -m database tests/database

sandbox-build:
	docker build -t codejudge-python-sandbox:phase2 sandbox/

sandbox-test:
	uv run pytest -v -m sandbox tests/sandbox
