.PHONY: sandbox-build sandbox-test

sandbox-build:
	docker build -t codejudge-python-sandbox:phase2 sandbox/

sandbox-test:
	pytest -v -m sandbox tests/sandbox

