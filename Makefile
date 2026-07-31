.PHONY: install check backend-check frontend-check docs-check

install:
	python -m pip install -e "./backend[dev]"
	npm --prefix frontend ci

check: backend-check frontend-check docs-check

backend-check:
	ruff check backend
	black --check backend
	mypy backend/app
	pytest backend

frontend-check:
	npm --prefix frontend run lint
	npm --prefix frontend run typecheck
	npm --prefix frontend test
	npm --prefix frontend run build

docs-check:
	npx --yes markdownlint-cli2 "**/*.md" "#frontend/node_modules"
