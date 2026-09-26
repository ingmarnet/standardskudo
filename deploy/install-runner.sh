#!/usr/bin/env bash
# Instalación ÚNICA en el servidor: runner self-hosted de GitHub + unit systemd
# de Skudo. Se corre una vez (luego GitHub despliega solo en cada push).
#
#   Uso: sudo bash deploy/install-runner.sh <TOKEN>
#
# El <TOKEN> se genera en GitHub: repo -> Settings -> Actions -> Runners ->
# "New self-hosted runner". Es efímero y NO debe commitearse.
set -euo pipefail

TOKEN="${1:?uso: install-runner.sh <TOKEN de registro del runner>}"

RUNNER_DIR="${SKUDO_RUNNER_DIR:-/opt/actions-runner}"
RUNNER_VERSION="${SKUDO_RUNNER_VERSION:-2.322.0}"
REPO_URL="https://github.com/ingmarnet/standardskudo"

# Al invocarse vía `sudo`, `config.sh` ve SUDO_USER y se niega ("Must not run
# with sudo"). Lo limpiamos: el proceso ya es root, sólo quitamos la marca.
unset SUDO_USER SUDO_UID SUDO_GID SUDO_COMMAND 2>/dev/null || true

# --- 1) runner self-hosted -------------------------------------------------
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
curl -o actions-runner.tar.gz -L \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
tar xzf actions-runner.tar.gz
./config.sh --unattended \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --name "$(hostname)" \
  --labels self-hosted,linux
./svc.sh install
./svc.sh start

# --- 2) servicio systemd de Skudo ------------------------------------------
install -m 0644 /opt/skudo/deploy/skudo.service /etc/systemd/system/skudo.service
systemctl daemon-reload
systemctl enable skudo
systemctl restart skudo

echo "Listo. El runner está activo y Skudo corre bajo systemd."
echo "Confirma el venv: si no está en /opt/skudo/.venv, ajustá ExecStart en skudo.service y SKUDO_VENV en deploy.sh."
