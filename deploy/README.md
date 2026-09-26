# Auto-deploy desde GitHub

Un runner self-hosted de GitHub Actions vive en el servidor (192.168.2.85) y, en
cada push a `main`, ejecuta `deploy/deploy.sh`: `git pull` → sync de dependencias
(si cambió el lock) → humo de import → `alembic upgrade head` → `systemctl restart
skudo` → health check. No se guarda ninguna clave SSH ni contraseña en GitHub: el
runner ya está dentro del servidor y usa el clon local de `/opt/skudo`.

## Instalación (una sola vez, en el servidor)

```bash
# 1. Confirmar el venv y ajustar si hace falta:
ls -l /opt/skudo/.venv/bin/skudo /opt/skudo/.venv/bin/alembic
# Si la ruta difiere, editar SKUDO_VENV en deploy/deploy.sh y ExecStart en
# deploy/skudo.service ANTES de instalar.

# 2. Instalar runner + unit systemd (el token sale de GitHub UI):
sudo bash /opt/skudo/deploy/install-runner.sh <TOKEN>

# 3. Verificar:
systemctl status skudo --no-pager
curl -fsS http://127.0.0.1:8000/docs >/dev/null && echo "HTTP ok"
```

Tras esto, cualquier push a `main` se despliega solo. Estado en la pestaña
"Actions" del repo; el job queda rojo si el health check no responde en 20s.

## Requisitos / notas

- El runner corre como `root` (el entorno hoy está 100% bajo root). Para
  endurecerlo, mover a un usuario `skudo` con `sudo` limitado a `systemctl
  restart skudo` y a `alembic`. `TODO` marcado en `skudo.service`.
- `EnvironmentFile=/etc/skudo/skudo.env` exige formato `CLAVE=valor` plano
  (systemd no soporta `export` ni expansión de shell). Verificar el archivo.
- `SKUDO_RUNNER_VERSION` en `install-runner.sh` es orientativo: si GitHub pide
  otra versión, copiar la URL exacta de la página "New self-hosted runner".
- El primer deploy (el que trae estos archivos) se hace a mano con `git pull` en
  `/opt/skudo`; de ahí en más es automático.
