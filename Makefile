.PHONY: lint test check build serve

lint:
	ruff check .
	ruff format --check .

test:
	python3 -m pytest -q

check: ## run the checks locally
	python3 -m statuspage.check sites.toml data/history.json

build:
	python3 -m statuspage.build data/history.json site

serve: build
	python3 -m http.server -d site 8000
