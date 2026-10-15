.PHONY: install dev build serve collect health prune test

install:
	uv sync --extra dev
	cd web && pnpm install

build:
	cd web && pnpm run build

dev:
	cd web && pnpm run dev

serve:
	uv run feicms serve

collect:
	uv run feicms collect

health:
	uv run feicms health

prune:
	uv run feicms prune

test:
	uv run pytest -q
