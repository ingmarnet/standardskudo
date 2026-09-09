up:
	docker compose up -d --wait

test: up
	uv run pytest -v

lint:
	uv run ruff check src tests

migrate: up
	SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo \
		uv run alembic upgrade head
