.PHONY: install dev test lint fmt bench serve ui-install ui-dev ui-build docker demo claude-doctor claude-dry-run

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

claude-doctor:      ## Claude Agent SDK provider check (no model calls)
	famas claude doctor --offline-test

claude-dry-run:     ## validation tests A, B, C through the Claude Agent SDK with scripted, offline responses
	FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true famas claude validate a
	FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true famas claude validate b
	FAMAS_LLM_PROVIDER=claude_agent_sdk FAMAS_CLAUDE_DRY_RUN=true famas claude validate c

serve:              ## API (+ built UI) on http://127.0.0.1:8000
	famas serve --reload

ui-install:
	cd frontend && npm ci

ui-dev:             ## Vite dev server on http://localhost:5173 (proxies /api to :8000)
	cd frontend && npm run dev

ui-build:
	cd frontend && npm run build

docker:
	docker compose up --build
