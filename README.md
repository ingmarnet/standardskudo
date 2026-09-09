# StandardSkudo

Plataforma SaaS multitenant para evaluar y corregir la **calidad del registro de
productos** en tiendas Magento, a scope de *store view*.

Une en un único objeto de análisis lo que hoy el mercado ofrece por separado:
coherencia verificable del registro de producto, SEO y encontrabilidad medida — con
remediación masiva bajo aprobación humana, control de concurrencia y verificación
independiente de que el cambio realmente se aplicó.

## Estado

En diseño. No hay código todavía.

## Documentación

- [Diseño de la plataforma](docs/superpowers/specs/2026-09-08-standardskudo-catalog-quality-design.md)
  — arquitectura, los 12 ejes de evaluación, motor de reglas, evidencia por dato,
  remediación segura, multitenancy y la descomposición en sub-proyectos S0–S7.
  Revisión 2 (2026-09-09) incorpora una revisión técnica externa; ver el registro de
  revisiones al final del documento.

## Qué resuelve

Un atributo filtrable vacío no es un problema estético: es un producto que el cliente
no puede encontrar. Un peso mal cargado no es un dato sucio: es un flete mal cotizado.
StandardStandardSkudo detecta esos defectos, los prioriza por impacto comercial y esfuerzo, y
propone las correcciones con la evidencia de dónde salió cada dato, para que un humano
las apruebe.

El diseño parte de que el mayor riesgo no es dejar de detectar un defecto, sino
**confundir un dato incorrecto con un dato desconocido o con una excepción válida**: una
corrección equivocada aplicada en masa destruye más valor que la ausencia de la
herramienta.
