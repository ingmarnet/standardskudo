# S0 — Verificación manual contra la instancia real

El arnés automático (`python -m skudo.acceptance.s0`) comprueba cinco
criterios, todos contra un espejo poblado únicamente por los ingestores, sin
un solo fixture sembrado a mano: espejo sincronizado, un registro por store
view con la población completa de cada tienda, procedencia de scope, efecto
de categoría —evaluado enteramente desde filas del espejo, con los productos
de website desconocido reportados como no evaluados y nunca como defecto
inventado (Task A7.3)— e identidad de opciones. Con esto quedan cubiertos
estructuralmente los cuatro criterios de aceptación del spec, salvo uno:
**la latencia de sincronización de deltas no se mide en ninguna parte**, ni en
el arnés ni en los tests: es el punto 4 de esta lista y hoy es la única vía de
conocer ese número.

Estas seis comprobaciones necesitan ojo humano y se hacen una vez, antes de
declarar S0 terminado.

1. **Procedencia de scope, muestra a mano.** Elegir 10 productos con override en BR
   y verificar en el admin de Magento que el valor que el espejo marca como `store`
   es exactamente el que la store view de BR muestra, y que el marcado como `global`
   no tiene override.
2. **Efecto de categoría.** En el tenant piloto las dos store views (1 `py` y
   3 `br`) comparten `root_category_id = 2` y viven en websites distintos
   (`base` y `website_br`), así que la condición de árbol NO discrimina entre
   ellas: lo que discrimina es el website del producto y el `is_active` de la
   categoría por tienda. Elegir un producto asignado solo al website de PY y
   comprobar que `derive_category_effect` devuelve `producto_fuera_del_website`
   para la store view de BR —no `fuera_del_arbol_de_la_tienda`, que en este
   tenant sería un diagnóstico falso—. Comprobarlo también en el frontend: el
   producto no debe aparecer en esa navegación.
3. **Opción traducida.** Localizar un atributo select con etiquetas distintas en PY y
   BR y confirmar que el espejo guarda un solo `option_id` con dos labels.
4. **SLA de delta.** Editar el nombre de un producto en el admin, ejecutar
   `delta_sync` y medir cuánto tarda el cambio en aparecer en el espejo. Anotar el
   número: es la línea base del SLA. Ninguna comprobación automática cubre esto,
   así que si este paso no se hace, el cuarto criterio del spec queda sin
   verificar. `sync_watermark.updated_at` registra cuándo se sincronizó bien por
   última vez y es la magnitud a partir de la cual se puede automatizar más
   adelante.
5. **Digest de reconciliación.** Ejecutar `reconcile` sobre el catálogo completo. Si
   el digest no cuadra con conteos iguales, revisar la colación de ordenación de SKUs
   entre PHP y Python (ver la nota de la Task 13).
6. **Serialización JSON de `labels` en el módulo, sobre el cable.** Nada del
   lado Python puede confirmar cómo llega `labels` sin desplegar el módulo:
   el arnés lee el espejo, no la respuesta HTTP cruda. Hay un riesgo concreto
   y ya conocido: `json_encode` de PHP colapsa un mapa entero-clave con claves
   consecutivas desde cero en un array JSON (`[...]`), no en un objeto
   (`{...}`). Task A1 tuvo que forzar `stdClass` para el mapa de etiquetas de
   opción precisamente porque **45.801 de las 50.545 opciones** de este
   catálogo tienen exactamente esa forma de clave, y la corrupción habría
   golpeado las etiquetas de Paraguay sin tocar las de Brasil. El primer
   despliegue real debe inspeccionar la respuesta cruda de `/attributes` y
   confirmar que `labels` llega como objeto JSON, no como arreglo.

Anotar los resultados en este archivo con fecha antes de pasar a S1.
