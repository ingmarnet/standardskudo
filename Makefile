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

# La suite de integración necesita una instancia ARRANCABLE (app/etc/env.php y
# base de datos), no sólo el autoloader: es la única que puede afirmar que la
# población que el módulo reporta es la que ESA instancia considera activa,
# porque el filtro de versión de Magento sólo existe al ensamblarse el SQL de
# verdad (ver Test/Integration/PopulationMatchesMagentoTest y el defecto C2).
# Sin SKUDO_MAGENTO_APP_ROOT esos tests se saltan solos, así que `test-php`
# sigue siendo verde en una máquina sin instancia.
#
#   make test-php-integration SKUDO_MAGENTO_APP_ROOT=/ruta/a/la/instancia
SKUDO_MAGENTO_APP_ROOT ?=

test-php-integration:
	cd magento-module && SKUDO_MAGENTO_APP_ROOT=$(SKUDO_MAGENTO_APP_ROOT) \
		$(MAGENTO_PHPUNIT) -c phpunit.xml --testsuite 'Standard_Skudo integration'
