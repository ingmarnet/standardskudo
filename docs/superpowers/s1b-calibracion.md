# S1b — Calibración de la inferencia contra el catálogo real

**Fecha:** 2026-09-17 · **Contra:** Renovapadel en el servidor de producción
([[skudo-servidor-produccion]]), 3.681 productos × 2 store views, 152 atributos, 8
attribute sets, 0 productos sin set.

## Qué se midió

Primera pasada real de `skudo profile` + `skudo rules infer` sobre datos del cliente
(antes sólo había fixtures). El perfilador tardó **6,3 s** y **112 MB** de RAM: liviano.

- **Perfil:** 8 particiones por store view (una por set), **0 subtipos** — para los 8 sets el
  perfilador no encontró un divisor que redujera la ambigüedad por encima de `MIN_GANANCIA`
  (4 "ganancia insuficiente", 4 "grupo pequeño"). Ambas store views con **digest idéntico**
  (reproducible). Conclusión: para este catálogo el set es la granularidad correcta; los
  umbrales de partición (`MIN_PARTICION=50`, `MAX_CARD=12`, `MIN_GANANCIA=0.05`) **no
  necesitan ajuste**.

- **Inferencia:** 560 reglas en borrador (320 obligatoriedad + 240 rango). **~la mitad era
  ruido de atributos de SISTEMA de Magento.**

## El hallazgo: la inferencia sobre atributos de sistema es ruido

Las reglas de mayor evidencia eran atributos que Magento **siempre** setea, no señales de
calidad:

- Obligatoriedad con `marcaria=0` (presentes al 100 %): `tax_class_id`, `page_layout`,
  `options_container`, `gift_message_available`, `msrp_display_actual_price_type`, `status`,
  `meta_keyword`… — 152 de las 320. No marcan a nadie: inútiles y ensucian la curación.
- Rango sobre campos de config: `tax_class_id [2,2]`, `status [1,1]`,
  `msrp_display_actual_price_type [0,0]`, `visibility [1,4]` — rangos degenerados o sin
  sentido.

Las reglas **genuinamente útiles** estaban ahí y son buenas (set 16, calzado): obligatoriedad
sobre `short_description` (marcaría 269), `price` (262), `description` (260), `ean_13` (264),
`color` (207), `genero` (207), `calce`, `tipo_de_suela`, `talla`, `material_mochila`.

## La decisión (cambio de código, no de umbral)

Los umbrales de confianza (`UMBRAL_ALTO=0.90`, `UMBRAL_MEDIO=0.70`, `MIN_EVIDENCIA=50`,
`MAX_RATIO_AMBIGUO=0.10`) **quedan como estaban**: producen bandas de confianza sensatas. Lo
que sobraba no era el umbral sino el **alcance de qué atributos considerar**. Se agregó a
`inference.py`:

1. **Denylist `ATRIBUTOS_DE_SISTEMA` + `PREFIJOS_DE_SISTEMA`** (`ts_dimensions_`, `aw_arp_`,
   `use_config_`, `links_`, `samples_`, `bundle_`, `giftcard_`): no se infiere ni
   obligatoriedad ni rango sobre ellos.
2. **Skip de obligatoriedad con `vacio==0`** (marcaría a nadie): re-inferible si mañana
   aparece un hueco.
3. **Skip de rango degenerado (`p05==p95`)**: un constante no es un rango de plausibilidad.

**El filtro correcto a largo plazo** es `is_user_defined` de Magento (un atributo de sistema
lo trae en `false`), que exige exponerlo en el módulo PHP y guardarlo en el espejo. El
denylist es el puente pragmático hasta entonces; es una constante del código, editable en el
diff como los umbrales, no configuración por tenant.

## Efecto medido

Antes: 560 reglas (≈50 % ruido). Después del filtro: el conjunto queda dominado por atributos
de calidad reales (descripción, precio, color, género, ean, talle, calce, tipo de suela), que
es lo curable. La re-inferencia sobre el servidor confirma la reducción.
