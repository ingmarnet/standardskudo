up:
	docker compose up -d --wait

test: up
	uv run pytest -v

lint:
	uv run ruff check src tests

migrate: up
	SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo_test \
		uv run alembic upgrade head

MAGENTO_PHPUNIT ?= /var/www/casanissei.com/v248/vendor/bin/phpunit

test-php:
	cd magento-module && $(MAGENTO_PHPUNIT) -c phpunit.xml
