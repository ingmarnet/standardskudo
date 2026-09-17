"""El seed curado de los requisitos de Google Shopping.

Datos, no lógica. Cada fila lleva su `note` con la razón. Los rangos de
categoría son sobre la taxonomía de Google Shopping (ids numéricos). El
universal tiene min/max None y aplica a cualquier categoría mapeada.

ARRANQUE MÍNIMO: universales + indumentaria + electrónica. Ampliar a los ~30
grupos del tenant es la Task 8, con la especificación de Google al lado.
"""

# Cada dict: category_group, min, max, google_attribute, requirement, axis,
# applicability, note.
SEED_FLOOR: list[dict] = [
    # --- Universales (aplican a toda categoría mapeada) ---
    {"category_group": "*", "min": None, "max": None, "google_attribute": "title",
     "requirement": "required", "axis": 1, "applicability": {},
     "note": "Google exige título en todo producto"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "description",
     "requirement": "required", "axis": 6, "applicability": {},
     "note": "Google exige descripción en todo producto"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "image_link",
     "requirement": "required", "axis": 7, "applicability": {},
     "note": "Google exige al menos una imagen"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "price",
     "requirement": "required", "axis": 8, "applicability": {},
     "note": "Google exige precio"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "availability",
     "requirement": "required", "axis": 8, "applicability": {},
     "note": "Google exige disponibilidad"},
    {"category_group": "*", "min": None, "max": None, "google_attribute": "gtin",
     "requirement": "recommended", "axis": 4,
     "applicability": {"solo_si": "fabricante_asigna_gtin"},
     "note": "recomendado; un producto sin GTIN asignado es caso previsto, no defecto"},
    # --- Indumentaria y accesorios (Apparel & Accessories, 166 y su subárbol) ---
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "color", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: color obligatorio para Shopping"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "size", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: talle obligatorio"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "gender", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: género obligatorio"},
    {"category_group": "Apparel & Accessories", "min": 166, "max": 8330,
     "google_attribute": "age_group", "requirement": "required", "axis": 3,
     "applicability": {}, "note": "indumentaria: grupo de edad obligatorio"},
    # --- Electrónica (Electronics, 222 y su subárbol) ---
    {"category_group": "Electronics", "min": 222, "max": 2082,
     "google_attribute": "gtin", "requirement": "required", "axis": 4,
     "applicability": {"solo_si": "fabricante_asigna_gtin"},
     "note": "electrónica de marca: GTIN obligatorio cuando el fabricante lo asigna"},
]
