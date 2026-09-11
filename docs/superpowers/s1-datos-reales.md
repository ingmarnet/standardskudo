# S1 — lo que los datos reales cambian del spec

Mediciones tomadas el 2026-09-10 contra el catálogo del tenant piloto, todas por
consultas de solo lectura. El spec de S1 se escribió con supuestos que estas cifras
corrigen; este documento recoge qué cambia y por qué.

**Estado:** incorporado al spec en la revisión 3 (2026-09-11). Este documento queda como
la evidencia de esos cambios; el spec es la autoridad.

---

## 1. Los attribute sets: peor de lo dicho, y mejor de lo que parecía

El spec asume "~100 attribute sets". La realidad:

| | |
|---|---|
| Definidos | 422 |
| **Con al menos un producto** | **298** |
| Con más de 100 productos | 148 |

Pero la cifra que decide el trabajo no es cuántos sets hay, sino **qué porción del
catálogo cubre curar los primeros N**:

| Sets curados | Productos cubiertos |
|---|---|
| 10 | **45,8 %** |
| 25 | **68,9 %** |
| 50 | **83,3 %** |

La distribución está concentrada, así que el criterio de aceptación de S1 no se rompe:
se reformula. Curar diez sets ya cubre casi la mitad del catálogo y cincuenta cubren
cinco sextos.

**Cambio al spec:** el criterio de aceptación de S1 decía "las reglas de los 10
attribute sets mayores se curan en una sesión de trabajo". Pasa a expresarse en
cobertura de producto, no en número de sets: *la curación alcanza el 45 % del catálogo
en una sesión y el 80 % en la primera semana*, con los sets ordenados por número de
productos. Medir el avance en sets premia curar sets vacíos; medirlo en cobertura
apunta el esfuerzo donde está el catálogo.

**Y 124 sets definidos sin un solo producto** son en sí un hallazgo del eje 11: modelo
de datos muerto que ensucia la curación y la navegación del admin.

---

## 2. Sólo 8 atributos filtrables de 1.066

Con 298 sets en uso y 228.881 productos, la navegación por capas de la tienda se apoya
en **ocho** atributos.

Consecuencias para S1, en dos direcciones opuestas:

- El detector de **filtro perdido** del eje 11 —atributo con buen dato, buena cobertura y
  demanda, que nadie marcó como filtrable— va a encenderse masivamente. Es el hallazgo
  con mejor relación entre esfuerzo y dinero de todo el sistema: activar un filtro es un
  cambio de configuración, y su efecto es que productos que existen empiezan a ser
  encontrables.
- Y por eso mismo **no puede entregarse como una lista de mil filas**. El eje 11 tiene
  que emitirlo ordenado por impacto y por categoría, y la primera entrega debería ser
  una decena de candidatos con su evidencia, no un volcado.

**Cambio al spec:** el eje 11 pasa a ser el primer hallazgo demostrable del producto, no
uno más de la lista. Y su criterio de aceptación de S1 se expresa en candidatos
aceptados por el equipo de catálogo, no en candidatos detectados.

---

## 3. El eje 2 no puede discriminar por árbol en este tenant

Las dos store views cuelgan de grupos cuya `root_category_id` es **2** — el mismo árbol.
La store view 1 (`py`) está en el website `base`; la 3 (`br`) en `website_br`.

Así que la condición de pertenencia al árbol de `derive_category_effect` es **idéntica**
para PY y BR y no distingue nada. Lo que discrimina es la asignación de website del
producto y el `is_active` de la categoría por tienda: 212 categorías tienen valor propio
en PY y 66 en BR.

**Cambio al spec:** la redacción del eje 2 asume que el árbol discrimina. Debe decir que
en una topología de tienda única con websites por mercado —que es la de este tenant y
probablemente la de varios— el discriminante es el website y la actividad por tienda, y
que la pertenencia al árbol sólo separa mercados cuando cada uno tiene su propia raíz.

---

## 4. El piso externo ya existe a medias, y su ausencia es un hallazgo

`Standard_GoogleCategory` —módulo propio del cliente— crea el atributo de categoría
`google_category_id_int` y **641 de las 1.276 categorías ya están mapeadas** a la
taxonomía de Google Shopping.

Eso importa porque los requisitos de Google varían por categoría de producto: una prenda
necesita talle y color, un electrodoméstico no. Ese mapeo es el dato que faltaba para
saber **qué exigirle a cada producto**, y existe, curado a mano, para la mitad del árbol.

**Cambio al spec:** el "piso externo" del apartado 6.2 deja de ser un catálogo de reglas
a construir y pasa a ser una lectura de ese atributo, con reglas por categoría de Google.
Y aparece un hallazgo del eje 11 que el spec no contemplaba: **las 635 categorías sin
mapear**, cada una un grupo de productos que no puede validarse contra ningún requisito
de canal. Completar ese mapeo es trabajo humano acotado y de alto apalancamiento: una
categoría mapeada habilita la validación de todos sus productos de golpe.

---

## 5. La demanda de búsqueda interna no está disponible

La fórmula de priorización del apartado 6.5 usa cuatro señales: ventas, búsqueda interna,
GA4 y stock/margen. La segunda **no existe hoy**: `search_query` tiene **2 filas** en toda
la instancia. Los índices `top_queries-*` de OpenSearch son del plugin de *query insights*
—consultas lentas del propio motor, para monitoreo— y no términos de usuarios.

Es una pérdida real, y no menor: la búsqueda interna era la única señal que probaba la
carencia **con demanda**, y sostenía dos cosas del spec —el cruce del eje 9 (el término
por el que la gente busca no aparece en el nombre) y la detección de atributos faltantes
del eje 11 (buscan "notebook ssd" y no existe atributo de disco)—.

**Cambio al spec:** la fórmula degrada con gracia por diseño, así que sigue funcionando
con ventas, GA4 y stock. Pero el apartado 6.5 debe declarar que en este tenant la señal
de búsqueda está ausente, y los dos detectores que dependían de ella quedan marcados como
no computables hasta que exista.

**Y una recomendación que vale más que el parche:** `Standard_SemanticSearch` es del
cliente. Instrumentarlo para registrar los términos buscados y, sobre todo, **las
búsquedas con cero resultados**, daría la señal más valiosa de todo el sistema. Una
búsqueda sin resultados es un cliente que quiso comprar algo y no lo encontró: es la
prueba directa, con demanda medida, de que un registro está mal hecho o de que falta un
atributo. Ninguna otra señal dice eso.

---

## 6. Hechos del entorno que S3 hereda

- **`Magento_Staging` está activo.** Las actualizaciones programadas entran en vigor por
  el paso del tiempo, sin evento, y pueden pisar un write-back en el futuro. El riesgo
  que el spec anotaba como hipotético está confirmado.
- **El scope de precio es Website**, con 165.610 filas de precio con override. Cualquier
  regla o corrección sobre precio debe operar en el scope correcto o corregirá el
  mercado equivocado.
- **MSI con tres stocks**: `base` → 2, `br` → 3, y el stock 1 no sirve a ningún website.
  La disponibilidad vendible es por website y ya se espeja así.
- **24 atributos con scope de website** (`is_global = 2`), `price` entre ellos.

---

## 7. Observación operativa ajena al producto

La base de producción tiene **83 tablas `__tmp` de changelog acumuladas, 162 MB** —
restos de reindexados por mview que nunca se limpiaron. No es de este producto, pero es
la misma clase de deuda que los índices huérfanos de OpenSearch registrados en el otro
proyecto del cliente, y conviene que alguien la barra.
