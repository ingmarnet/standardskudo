# StandardSkudo

Plataforma SaaS multitenant para evaluar y corregir la **calidad del registro de
productos** en tiendas Magento, a scope de *store view*.

Une en un único objeto de análisis lo que hoy el mercado ofrece por separado:
coherencia verificable del registro de producto, SEO y encontrabilidad medida — con
remediación masiva bajo aprobación humana, control de concurrencia y verificación
independiente de que el cambio realmente se aplicó.

## Estado

S0 en curso: módulo Magento `Standard_Skudo` con ocho endpoints de lectura
(entorno, productos, productos por SKU, deltas, señales, checksums, atributos
y categorías), más un ingestor y espejo canónico en Python. Cubierto por una
suite de tests automatizada en ambos lados — `uv run pytest` para el lado
Python, `make test-php` para el módulo Magento.

## Uso del ingestor

Un solo punto de entrada, `python -m skudo.cli` (o `skudo` si el paquete está
instalado). El token NUNCA se pasa por la línea de comandos: la fila del
tenant guarda el NOMBRE de la variable de entorno donde vive.

```bash
export SKUDO_DATABASE_URL=postgresql+psycopg://skudo:skudo@localhost:55432/skudo
export SKUDO_TENANT_NISSEI_TOKEN=...        # el token, sólo en el entorno

python -m skudo.cli register-tenant --code nissei --base-url https://tienda.example
python -m skudo.cli probe       --tenant nissei      # sonda y espeja la topología
python -m skudo.cli attributes  --tenant nissei      # ANTES de los productos
python -m skudo.cli categories  --tenant nissei
python -m skudo.cli full-sync   --tenant nissei      # reanudable; --restart la reinicia
python -m skudo.cli signals     --tenant nissei
python -m skudo.cli delta-sync  --tenant nissei      # incremental, por cron
python -m skudo.cli reconcile   --tenant nissei      # sale != 0 si hay deriva
python -m skudo.cli repair      --tenant nissei --store 1 --from-reconcile
python -m skudo.cli status      --tenant nissei
python -m skudo.cli accept      --tenant nissei      # criterios de aceptación de S0
```

Códigos de salida (`skudo/exit_codes.py`): `0` éxito, `1` fallo de la
operación o deriva detectada, `2` tenant desconocido, `3` configuración
incompleta, `64` error de uso.

`full-sync` confirma por página y guarda su punto de reanudación: si se corta,
la próxima invocación continúa la MISMA generación desde el cursor guardado, y
el barrido de filas rancias sólo corre para una store view cuya pasada llegó a
la última página. `status` dice si hay una pasada a medias.

En el lado Magento, la cola de cambios se poda con
`bin/magento skudo:changelog:prune` (o su cron diario), que borra sólo filas
que el ingestor ya consumió.

## Documentación

- [Diseño de la plataforma](docs/superpowers/specs/2026-09-08-standardskudo-catalog-quality-design.md)
  — arquitectura, los 12 ejes de evaluación, motor de reglas, evidencia por dato,
  remediación segura, multitenancy y la descomposición en sub-proyectos S0–S7.
  Revisión 2 (2026-09-09) incorpora una revisión técnica externa; ver el registro de
  revisiones al final del documento.

## Qué resuelve

Un atributo filtrable vacío no es un problema estético: es un producto que el cliente
no puede encontrar. Un peso mal cargado no es un dato sucio: es un flete mal cotizado.
StandardSkudo detecta esos defectos, los prioriza por impacto comercial y esfuerzo, y
propone las correcciones con la evidencia de dónde salió cada dato, para que un humano
las apruebe.

El diseño parte de que el mayor riesgo no es dejar de detectar un defecto, sino
**confundir un dato incorrecto con un dato desconocido o con una excepción válida**: una
corrección equivocada aplicada en masa destruye más valor que la ausencia de la
herramienta.
