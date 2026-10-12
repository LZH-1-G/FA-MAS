.PHONY: install dev test lint fmt bench serve ui-install ui-dev ui-build docker demo

install:            ## install the package with all extras
	pip install -e ".[all]"

test:               ## run the test suite
	pytest

lint:               ## static checks
	ruff check famas tests

fmt:
	ruff format famas tests && ruff check --fix famas tests

bench:              ## full baseline comparison (deterministic simulation) + charts
	famas bench --plots --out benchmarks/results

demo:               ## one run in the terminal: FA-MAS recovering from silent upstream corruption
	famas run "Analyze LangGraph, CrewAI, AutoGen, and Microsoft Agent Framework. Compare their system architectures, agent communication mechanisms, scheduling strategies, and failure recovery capabilities." --scenario corruption --out report.md

serve:              ## API (+ built UI) on http://127.0.0.1:8000
	famas serve --reload

ui-install:
	cd frontend && npm install

ui-dev:             ## Vite dev server on http://localhost:5173 (proxies /api to :8000)
	cd frontend && npm run dev

ui-build:
	cd frontend && npm run build

docker:
	docker compose up --build
