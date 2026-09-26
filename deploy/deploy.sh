#!/usr/bin/env bash
# Despliegue idempotente de Skudo. Lo invoca el runner self-hosted de GitHub
# Actions en cada push a main. Se ejecuta desde el checkout del runner, pero
# opera sobre /opt/skudo (el clon que sirve producción).
set -euo pipefail

APP_DIR="${SKUDO_APP_DIR:-/opt/skudo}"
VENV="${SKUDO_VENV:-/opt/skudo/.venv}"

cd "$APP_DIR"

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

# Reinicio y verificación.
systemctl restart skudo

for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/docs >/dev/null 2>&1; then
    echo "Skudo desplegado y respondiendo (HEAD=$(git rev-parse --short HEAD))"
    exit 0
  fi
  sleep 1
done

echo "ERROR: el servicio no respondio tras 20s (HEAD=$(git rev-parse --short HEAD))" >&2
exit 1
