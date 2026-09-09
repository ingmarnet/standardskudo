# S0 — Verificación manual contra la instancia real

El arnés automático (`python -m skudo.acceptance.s0`) cubre los cuatro criterios
del spec. Estas cinco comprobaciones necesitan ojo humano y se hacen una vez,
antes de declarar S0 terminado.

1. **Procedencia de scope, muestra a mano.** Elegir 10 productos con override en BR
   y verificar en el admin de Magento que el valor que el espejo marca como `store`
   es exactamente el que la store view de BR muestra, y que el marcado como `global`
   no tiene override.
2. **Efecto de categoría.** Elegir un producto asignado a una categoría que cuelga
   del árbol de PY y comprobar que `derive_category_effect` devuelve
   `fuera_del_arbol_de_la_tienda` para la store view de BR. Comprobarlo también en
   el frontend: el producto no debe aparecer en esa navegación.
3. **Opción traducida.** Localizar un atributo select con etiquetas distintas en PY y
   BR y confirmar que el espejo guarda un solo `option_id` con dos labels.
4. **SLA de delta.** Editar el nombre de un producto en el admin, ejecutar
   `delta_sync` y medir cuánto tarda el cambio en aparecer en el espejo. Anotar el
   número: es la línea base del SLA.
5. **Digest de reconciliación.** Ejecutar `reconcile` sobre el catálogo completo. Si
   el digest no cuadra con conteos iguales, revisar la colación de ordenación de SKUs
   entre PHP y Python (ver la nota de la Task 13).

Anotar los resultados en este archivo con fecha antes de pasar a S1.
