#!/usr/bin/env bash
# Despliegue idempotente de Skudo. Lo invoca el runner self-hosted de GitHub
# Actions en cada push a main. Se ejecuta desde el checkout del runner, pero
# opera sobre /opt/skudo (el clon que sirve producción).
set -euo pipefail

APP_DIR="${SKUDO_APP_DIR:-/opt/skudo}"
VENV="${SKUDO_VENV:-/opt/skudo/.venv}"
ENV_FILE="${SKUDO_ENV_FILE:-/etc/skudo/skudo.env}"

cd "$APP_DIR"

# Cargar entorno (SKUDO_DATABASE_URL, tokens). El runner no hereda estas vars;
# alembic y el humo de import las necesitan. `set -a` para exportar al sourcear.
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

PREV=$(git rev-parse HEAD)
git fetch origin main
git reset --hard origin/main

# Re-sincronizar dependencias sólo si cambió el manifiesto o el lock.
CHANGED=$(git diff --name-only "$PREV" HEAD -- pyproject.toml uv.lock || true)
if [ -n "$CHANGED" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv sync --frozen
  else
    "$VENV/bin/pip" install -e . --quiet
  fi
fi

# Humo: si el paquete no importa, no reiniciamos el servicio.
"$VENV/bin/python" -c "import skudo.web.app"

# Migraciones de esquema.
"$VENV/bin/alembic" upgrade head

# Reinicio (el runner corre como ingmar: sudo NOPASSWD acotado a este comando).
if [ "$(id -u)" -eq 0 ]; then
  systemctl restart skudo
else
  sudo -n systemctl restart skudo
fi

for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/docs >/dev/null 2>&1; then
    echo "Skudo desplegado y respondiendo (HEAD=$(git rev-parse --short HEAD))"
    exit 0
  fi
  sleep 1
done

echo "ERROR: el servicio no respondio tras 20s (HEAD=$(git rev-parse --short HEAD))" >&2
exit 1
