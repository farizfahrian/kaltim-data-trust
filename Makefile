.PHONY: install test lint build-snapshot web-check web-build ci

install:
	cd pipeline && uv sync --extra dev --extra postgres
	cd web && npm install

lint:
	cd pipeline && uv run ruff check src tests

test:
	cd pipeline && uv run pytest -v

build-snapshot:
	cd pipeline && uv run python -m kdt.cli build-official

web-check:
	cd web && npm run check

web-build:
	cd web && npm run build

ci: lint test web-check web-build
