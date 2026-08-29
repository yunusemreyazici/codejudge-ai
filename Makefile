.PHONY: ai-test ai-worker-e2e database-migrate database-test infra-up queue-test sandbox-build sandbox-test worker worker-e2e

infra-up:
	docker compose up -d postgres redis

database-migrate:
	uv run alembic upgrade head

database-test:
	CODEJUDGE_REQUIRE_DATABASE=1 uv run pytest -v -m database tests/database

queue-test:
	CODEJUDGE_REQUIRE_DATABASE=1 uv run pytest -v -m queue tests/queue

worker:
	uv run codejudge-worker

worker-e2e:
	CODEJUDGE_REQUIRE_DATABASE=1 CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_worker_e2e.py

ai-test:
	uv run pytest -v -m ai tests/ai

ai-worker-e2e:
	CODEJUDGE_REQUIRE_DATABASE=1 CODEJUDGE_REQUIRE_DOCKER=1 uv run pytest -v tests/queue/test_ai_worker_e2e.py

sandbox-build:
	docker build -t codejudge-python-sandbox:phase2 sandbox/

sandbox-test:
	uv run pytest -v -m sandbox tests/sandbox
