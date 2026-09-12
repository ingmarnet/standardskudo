"""La invariante: después de un ciclo de ingesta completo, el espejo no tiene
NI UNA fila huérfana.

Por qué una invariante del ESPEJO y no una prueba por función. Este
sub-proyecto ya envió tres veces un hueco que una prueba por función no podía
ver: cuatro tablas cuyos `upsert_*` sólo se llamaban desde tests (C1), luego
`product_signal` en la misma fase que arreglaba las otras cuatro (C3), y ahora
M3 —tres tablas sin barrido, con 457.762 filas huérfanas medidas—. En los tres
casos cada función hacía exactamente lo que su prueba decía. Lo que faltaba era
alguien que mirara el espejo ENTERO y preguntara si sus filas se sostienen unas
a otras.

Eso es lo que hace este archivo, en tres capas:

1. `test_a_complete_ingest_cycle_leaves_no_orphans` recorre el ciclo completo
   —atributos, categorías, pasada completa, señales, un delta que BORRA un
   producto, y las segundas pasadas de atributos y categorías que barren lo que
   el origen dejó de ofrecer— y exige cero huérfanos en TODAS las aristas.
2. `test_the_invariant_detects_an_orphan_of_every_class` siembra a mano un
   huérfano de cada clase y exige que el detector lo vea. Sin esto, la
   invariante podría estar pasando por estar mal escrita.
3. `test_every_mirror_table_declares_its_referential_status` obliga a que una
   tabla NUEVA del espejo se declare: o es raíz, o es hija de una arista. Es la
   capa que hace que esto siga sirviendo cuando alguien agregue la próxima
   tabla, en vez de envejecer como una lista escrita a mano una vez.

Las consultas de acá se escriben con LEFT JOIN ... IS NULL a propósito, no con
el `NOT EXISTS` que usa la limpieza del producto: si compartieran predicado,
un error en el predicado dejaría pasar a los dos a la vez.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from skudo_testing import skudo_response
from sqlalchemy import func, select

from skudo.ingest.attribute_sync import sync_attributes
from skudo.ingest.category_sync import sync_categories
from skudo.ingest.delta_sync import delta_sync
from skudo.ingest.full_sync import full_sync
from skudo.ingest.signal_sync import sync_signals
from skudo.ingest.source import TenantSource
from skudo.mirror.models import (
    Attribute,
    AttributeOption,
    AttributeOptionLabel,
    Base,
    Category,
    CategoryStoreState,
    ProductCategoryAssignment,
    ProductRecord,
    ProductSignal,
    Tenant,
)

STORE_VIEWS = [1, 3]


# --- las aristas del espejo -------------------------------------------------


def _orphan_assignments(session, tenant_id) -> list[str]:
    """Clase 1 de M3: asignación de categoría de un SKU sin `product_record`.

    "Sin registro en NINGUNA store view": la asignación es global, así que un
    producto vivo en BR y retirado de PY conserva la suya legítimamente.
    """
    rows = session.execute(
        select(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
        .outerjoin(
            ProductRecord,
            (ProductRecord.tenant_id == ProductCategoryAssignment.tenant_id)
            & (ProductRecord.sku == ProductCategoryAssignment.sku),
        )
        .where(ProductCategoryAssignment.tenant_id == tenant_id, ProductRecord.id.is_(None))
        .distinct()
    ).all()
    return [f"asignación {sku} -> categoría {category}" for sku, category in rows]


def _orphan_signals(session, tenant_id) -> list[str]:
    """Señal comercial de un SKU sin `product_record`.

    No es una de las tres clases que M3 nombra, y se vigila igual: S1 prioriza
    los hallazgos POR señal, así que un SKU fantasma con facturación no sería
    un hallazgo fabricado más — sería el PRIMERO que un humano ve.
    """
    rows = session.execute(
        select(ProductSignal.sku, ProductSignal.store_view_magento_id)
        .outerjoin(
            ProductRecord,
            (ProductRecord.tenant_id == ProductSignal.tenant_id)
            & (ProductRecord.sku == ProductSignal.sku),
        )
        .where(ProductSignal.tenant_id == tenant_id, ProductRecord.id.is_(None))
        .distinct()
    ).all()
    return [f"señal {sku} (store {store})" for sku, store in rows]


def _orphan_options(session, tenant_id) -> list[str]:
    """Clase 2 de M3: opción de un atributo que ya no está en el espejo."""
    rows = session.execute(
        select(AttributeOption.attribute_code, AttributeOption.magento_option_id)
        .outerjoin(
            Attribute,
            (Attribute.tenant_id == AttributeOption.tenant_id)
            & (Attribute.code == AttributeOption.attribute_code),
        )
        .where(AttributeOption.tenant_id == tenant_id, Attribute.id.is_(None))
    ).all()
    return [f"opción {option} del atributo {code}" for code, option in rows]


def _orphan_option_labels(session, tenant_id) -> list[str]:
    """Clase 2 de M3, la mitad que se lleva la identidad: etiqueta de una
    opción que ya no existe. `attribute_option_label` no tiene `tenant_id`
    propio, así que la pregunta se hace por su opción y las etiquetas sin
    opción ALGUNA se cuentan siempre (no se pueden atribuir a un tenant, y
    ninguna debería existir)."""
    stray = session.scalars(
        select(AttributeOptionLabel.id)
        .outerjoin(AttributeOption, AttributeOption.id == AttributeOptionLabel.option_row_id)
        .where(AttributeOption.id.is_(None))
    ).all()
    return [f"etiqueta {label_id} sin opción" for label_id in stray]


def _orphan_category_states(session, tenant_id) -> list[str]:
    """Clase 3 de M3: estado por tienda de una categoría que ya no está."""
    rows = session.execute(
        select(
            CategoryStoreState.category_magento_id,
            CategoryStoreState.store_view_magento_id,
        )
        .outerjoin(
            Category,
            (Category.tenant_id == CategoryStoreState.tenant_id)
            & (Category.magento_id == CategoryStoreState.category_magento_id),
        )
        .where(CategoryStoreState.tenant_id == tenant_id, Category.id.is_(None))
    ).all()
    return [f"estado de la categoría {category} en la tienda {store}" for category, store in rows]


def _assignments_to_unmirrored_categories(session, tenant_id) -> list[str]:
    """Asignación a una categoría que el espejo no tiene.

    NO hay limpieza para esto, a propósito, y por eso se vigila desde acá: la
    tabla `category` la puebla una pasada INDEPENDIENTE de la de productos, así
    que tratar "categoría desconocida" como huérfana borraría la tabla entera
    de asignaciones de un tenant que todavía no corrió `skudo categories`.
    Ese es exactamente el colapso de "desconocido" en "incorrecto" que la
    sección 1 del spec nombra como el mayor riesgo del sistema.

    En un ciclo COMPLETO la arista se sostiene sola —el payload de `/products`
    es la verdad de las dos mitades— así que esta prueba la exige igual: si un
    camino futuro rompiera esa coherencia, acá se ve.
    """
    rows = session.execute(
        select(ProductCategoryAssignment.sku, ProductCategoryAssignment.category_magento_id)
        .outerjoin(
            Category,
            (Category.tenant_id == ProductCategoryAssignment.tenant_id)
            & (Category.magento_id == ProductCategoryAssignment.category_magento_id),
        )
        .where(ProductCategoryAssignment.tenant_id == tenant_id, Category.id.is_(None))
    ).all()
    return [f"asignación {sku} -> categoría {category} inexistente" for sku, category in rows]


# Nombre de la arista -> (tabla hija, consulta). La tabla hija se declara acá y
# no en una lista paralela: `CHILD_TABLES` se DERIVA de esto, así que una
# arista nueva no puede quedarse sin declarar a quién vigila.
EDGES = {
    "asignaciones sin producto (M3 clase 1)": (
        "product_category_assignment",
        _orphan_assignments,
    ),
    "señales sin producto": ("product_signal", _orphan_signals),
    "opciones sin atributo (M3 clase 2)": ("attribute_option", _orphan_options),
    "etiquetas sin opción (M3 clase 2)": (
        "attribute_option_label",
        _orphan_option_labels,
    ),
    "estados sin categoría (M3 clase 3)": (
        "category_store_state",
        _orphan_category_states,
    ),
    "asignaciones a categorías no espejadas": (
        "product_category_assignment",
        _assignments_to_unmirrored_categories,
    ),
}

CHILD_TABLES = {child for child, _check in EDGES.values()}

# Tablas que NO cuelgan de ninguna otra fila del espejo: su única referencia es
# el tenant, que la FK de la base ya garantiza. Declararlas es lo que obliga a
# que una tabla nueva pase por acá.
ROOT_TABLES = {
    "tenant",
    "environment_snapshot",
    "website",
    "store_group",
    "store_view",
    "attribute_set",
    "attribute",
    "product_record",
    "category",
    "sync_watermark",
    "full_sync_checkpoint",
    "sync_pass",
}

# Las tablas del perfil (S1a) declaran su situación de otra forma: tienen
# CLAVES FORÁNEAS de verdad. Las del espejo no pueden tenerlas —`sku` y
# `category_magento_id` son referencias lógicas a un catálogo ajeno que puede
# llegar en cualquier orden—, y por eso necesitan estas aristas vigiladas. El
# perfil es nuestro de punta a punta, así que la base rechaza la huérfana en
# vez de dejarla pasar, y el test de abajo comprueba que la FK existe de
# verdad en lugar de creerle a esta lista.
FK_ENFORCED_TABLES = {
    "profile_run",
    "profile_partition",
    "profile_attribute_coverage",
    "profile_value_stats",
}


def mirror_orphans(session, tenant_id: int) -> dict[str, list[str]]:
    """Todas las aristas rotas del espejo de un tenant, por clase."""
    return {
        name: found
        for name, (_child, check) in EDGES.items()
        if (found := check(session, tenant_id))
    }


# --- el ciclo de ingesta completo -------------------------------------------


def _product(sku: str, categories: list[int]) -> dict:
    return {
        "sku": sku, "mpn": None, "model": None, "gtin": None, "variant_key": None,
        "attribute_set_id": 4, "type_id": "simple",
        "global_values": {"name": sku, "color": "500"},
        "store_values": {}, "website_ids": [1], "category_ids": categories,
        "updated_at": "2026-09-01 10:00:00",
    }


def _attribute(code: str, option_ids: list[int]) -> dict:
    return {
        "code": code, "label": code.title(), "frontend_input": "select",
        "declared_scope": "global", "is_filterable": True, "is_required": False,
        "attribute_set_ids": [4],
        "options": [
            {"option_id": option_id, "labels": {"0": f"o{option_id}", "3": f"p{option_id}"}}
            for option_id in option_ids
        ],
    }


def _category(category_id: int) -> dict:
    return {
        "category_id": category_id, "path": [1, 2, category_id],
        "default_name": f"Cat {category_id}",
        "store_states": [
            {"store_id": store, "is_active": True, "name": f"Cat {category_id}"}
            for store in STORE_VIEWS
        ],
    }


# La misma sonda que el resto de la suite: la forma del payload de
# `/environment` vive en un fixture único para que ningún doble describa otra.
ENVIRONMENT = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "environment_opensource.json").read_text()
)


class FakeInstance:
    """Una instancia de Magento cuyo catálogo se puede cambiar entre pasadas.

    Es lo que permite que este archivo ejerza un CICLO —el origen cambia y el
    espejo tiene que seguirlo— y no una foto.
    """

    def __init__(self):
        self.products: dict[str, list[int]] = {}
        self.attributes: list[dict] = []
        self.categories: list[int] = []
        self.signals: dict[int, list[dict]] = {}
        self.changes: list[dict] = []

    def source(self, tenant_id: int) -> TenantSource:
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/environment"):
                return skudo_response(ENVIRONMENT)
            if path.endswith("/products"):
                return skudo_response(
                    {
                        "items": [
                            _product(sku, cats) for sku, cats in sorted(self.products.items())
                        ],
                        "next_cursor": None,
                    }
                )
            if path.endswith("/products-by-sku"):
                asked = json.loads(request.read().decode())["skus"]
                return skudo_response(
                    {
                        "items": [
                            _product(sku, self.products[sku])
                            for sku in asked
                            if sku in self.products
                        ]
                    }
                )
            if path.endswith("/attributes"):
                return skudo_response({"items": self.attributes, "next_cursor": None})
            if path.endswith("/categories"):
                return skudo_response(
                    {
                        "items": [_category(cat) for cat in self.categories],
                        "next_cursor": None,
                    }
                )
            if path.endswith("/signals"):
                store = int(request.url.params.get("storeId", 0))
                return skudo_response({"items": self.signals.get(store, [])})
            if path.endswith("/deltas"):
                since = int(request.url.params.get("sinceId", 0))
                pending = [c for c in self.changes if c["change_id"] > since]
                if not pending:
                    return skudo_response({"items": [], "last_change_id": None})
                return skudo_response(
                    {"items": pending, "last_change_id": pending[-1]["change_id"]}
                )
            return httpx.Response(404)

        return TenantSource.from_tenant(
            SimpleNamespace(id=tenant_id, base_url="https://x.test"),
            token="token",
            transport=httpx.MockTransport(handler),
        )


def _run_full_cycle(session, tenant_id: int) -> FakeInstance:
    """El ciclo entero, con el origen CAMBIANDO a mitad.

    El orden es el de producción: atributos antes de la pasada completa (el
    mapa de scopes que `resolve_scope` necesita), categorías, pasada completa,
    señales, y después los cambios.
    """
    instance = FakeInstance()
    instance.attributes = [_attribute("color", [500, 501]), _attribute("talle", [700])]
    instance.categories = [7, 8]
    instance.products = {"SKU-A": [7], "SKU-B": [7], "SKU-QUE-SE-VA": [8]}
    instance.signals = {
        1: [{"sku": "SKU-A", "units_sold": 3, "revenue": "100.0", "salable_qty": 1.0,
             "physical_qty": 1.0, "uses_msi": False, "margin": None, "search_demand": 2},
            {"sku": "SKU-QUE-SE-VA", "units_sold": 9, "revenue": "900.0",
             "salable_qty": 1.0, "physical_qty": 1.0, "uses_msi": False,
             "margin": None, "search_demand": 5}],
        3: [],
    }

    sync_attributes(session, instance.source(tenant_id))
    sync_categories(session, instance.source(tenant_id), STORE_VIEWS)
    full_sync(session, instance.source(tenant_id), STORE_VIEWS)
    sync_signals(session, instance.source(tenant_id), STORE_VIEWS)

    # El origen cambia: se borra un producto, desaparecen un atributo y una
    # opción, y la categoría que sólo usaba el producto borrado.
    del instance.products["SKU-QUE-SE-VA"]
    instance.changes = [
        {"change_id": 1, "sku": "SKU-QUE-SE-VA", "event": "delete",
         "changed_at": "2026-09-05 08:00:00"}
    ]
    delta_sync(session, instance.source(tenant_id), STORE_VIEWS)

    instance.attributes = [_attribute("color", [500])]
    instance.categories = [7]
    sync_attributes(session, instance.source(tenant_id))
    sync_categories(session, instance.source(tenant_id), STORE_VIEWS)
    full_sync(session, instance.source(tenant_id), STORE_VIEWS, restart=True)

    return instance


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def other_tenant(db_session):
    row = Tenant(code="otro", name="Otro", base_url="https://y.test", token_env_var="T2")
    db_session.add(row)
    db_session.flush()
    return row


def _counts(session, tenant_id: int) -> dict[str, int]:
    tables = {
        "product_record": ProductRecord,
        "product_category_assignment": ProductCategoryAssignment,
        "product_signal": ProductSignal,
        "attribute": Attribute,
        "attribute_option": AttributeOption,
        "category": Category,
        "category_store_state": CategoryStoreState,
    }
    return {
        name: session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
        )
        for name, model in tables.items()
    }


def test_a_complete_ingest_cycle_leaves_no_orphans(db_session, tenant):
    """LA invariante. Si un camino de escritura futuro se olvida de limpiar lo
    que borró, esta prueba falla aunque la prueba de ESE camino siga verde."""
    _run_full_cycle(db_session, tenant.id)

    assert mirror_orphans(db_session, tenant.id) == {}


def test_the_cycle_really_removed_things(db_session, tenant):
    """La invariante de arriba se cumple trivialmente sobre un espejo que nunca
    borró nada. Esto exige que el ciclo haya ejercido los tres barridos: el
    producto, el atributo con su opción, y la categoría con sus estados."""
    _run_full_cycle(db_session, tenant.id)

    counts = _counts(db_session, tenant.id)
    assert counts["product_record"] == 4  # SKU-A y SKU-B en dos store views
    assert counts["product_category_assignment"] == 2
    assert counts["product_signal"] == 1  # la del producto borrado se fue
    assert counts["attribute"] == 1  # 'talle' se barrió
    assert counts["attribute_option"] == 1  # 500; 501 y 700 se barrieron
    assert counts["category"] == 1  # la 8 se barrió
    assert counts["category_store_state"] == 2  # las de la 8 se fueron


def test_the_cycle_of_one_tenant_leaves_the_other_tenants_mirror_intact(
    db_session, tenant, other_tenant
):
    """Cada barrido y cada limpieza de este cierre es un DELETE sobre tablas
    compartidas. El ciclo entero de un tenant no puede mover ni una fila del
    otro: se compara el espejo completo del otro antes y después."""
    _run_full_cycle(db_session, other_tenant.id)
    before = _counts(db_session, other_tenant.id)

    _run_full_cycle(db_session, tenant.id)

    assert _counts(db_session, other_tenant.id) == before
    assert mirror_orphans(db_session, other_tenant.id) == {}


@pytest.mark.parametrize(
    "orphan_class",
    [
        "asignaciones sin producto (M3 clase 1)",
        "señales sin producto",
        "opciones sin atributo (M3 clase 2)",
        "estados sin categoría (M3 clase 3)",
        "asignaciones a categorías no espejadas",
    ],
)
def test_the_invariant_detects_an_orphan_of_every_class(db_session, tenant, orphan_class):
    """Una invariante que no sabe fallar no afirma nada. Se siembra a mano un
    huérfano de cada clase —saltándose los caminos de ingesta, que es
    precisamente lo que hace un `INSERT` a mano o un camino futuro con un
    hueco— y se exige que el detector lo nombre."""
    _run_full_cycle(db_session, tenant.id)
    assert mirror_orphans(db_session, tenant.id) == {}

    saboteurs = {
        "asignaciones sin producto (M3 clase 1)": lambda: db_session.add(
            ProductCategoryAssignment(tenant_id=tenant.id, sku="FANTASMA", category_magento_id=7)
        ),
        "señales sin producto": lambda: db_session.add(
            ProductSignal(tenant_id=tenant.id, sku="FANTASMA", store_view_magento_id=1,
                          units_sold=1, uses_msi=False)
        ),
        "opciones sin atributo (M3 clase 2)": lambda: db_session.add(
            AttributeOption(tenant_id=tenant.id, attribute_code="inexistente",
                            magento_option_id=9, sync_generation=0)
        ),
        "estados sin categoría (M3 clase 3)": lambda: db_session.add(
            CategoryStoreState(tenant_id=tenant.id, category_magento_id=999,
                               store_view_magento_id=1, is_active=True, name="Fantasma",
                               sync_generation=0)
        ),
        "asignaciones a categorías no espejadas": lambda: db_session.add(
            ProductCategoryAssignment(tenant_id=tenant.id, sku="SKU-A", category_magento_id=999)
        ),
    }
    saboteurs[orphan_class]()
    db_session.flush()

    assert orphan_class in mirror_orphans(db_session, tenant.id)


def test_every_mirror_table_declares_its_referential_status():
    """La capa que hace que esto no envejezca. Una tabla NUEVA del espejo tiene
    que declararse: o es raíz —su única referencia es el tenant— o es hija de
    una arista vigilada. Si alguien agrega una tabla y no pasa por acá, esta
    prueba falla, que es cómo se entera de que le falta decidir si sus filas
    pueden quedar huérfanas.

    Las tres rondas de este defecto (C1, C3 y M3) fueron exactamente eso: una
    tabla del esquema sobre la que nadie se hizo esta pregunta.
    """
    declared = ROOT_TABLES | CHILD_TABLES | FK_ENFORCED_TABLES
    tables = set(Base.metadata.tables)

    assert tables - declared == set(), (
        "estas tablas no declaran su situación referencial: agregalas a "
        "ROOT_TABLES (su única referencia es el tenant), a CHILD_TABLES con una "
        "arista en EDGES que vigile sus huérfanas, o a FK_ENFORCED_TABLES si la "
        "base impide la huérfana con una clave foránea."
    )
    assert declared - tables == set(), (
        "estas tablas se declaran pero ya no existen en el modelo"
    )


def test_the_fk_enforced_tables_really_have_those_foreign_keys():
    """Declararse en `FK_ENFORCED_TABLES` es una AFIRMACIÓN sobre el esquema, y
    sin esta prueba sería la forma barata de sacarse de encima la pregunta: una
    tabla nueva sin FK aterrizaría ahí y quedaría fuera de las dos capas de
    vigilancia. Acá se comprueba que la clave foránea existe y apunta a una
    tabla que existe."""
    for name in sorted(FK_ENFORCED_TABLES):
        tabla = Base.metadata.tables[name]
        destinos = {fk.column.table.name for fk in tabla.foreign_keys}
        assert destinos, (
            f"{name} se declara protegida por clave foránea y no tiene ninguna"
        )
        assert destinos <= set(Base.metadata.tables), (
            f"{name} referencia tablas que no existen: {destinos}"
        )


def test_the_database_itself_refuses_a_label_without_an_option(db_session, tenant):
    """La etiqueta huérfana es la única de las clases vigiladas que no se puede
    sembrar a mano: `attribute_option_label.option_row_id` es una FK y la base
    la rechaza. Eso no vuelve irrelevante la arista — la vuelve un modo de
    fallo distinto: si el barrido de opciones se olvidara de borrar primero las
    etiquetas, no dejaría huérfanas, fallaría con una violación de FK. Se
    afirma acá para que la arista quede explicada en vez de parecer
    inalcanzable por descuido.
    """
    from sqlalchemy.exc import IntegrityError

    db_session.add(
        AttributeOptionLabel(option_row_id=999_999, store_view_magento_id=1, label="Negro")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
