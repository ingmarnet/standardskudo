"""Ingestor de atributos, opciones y etiquetas por store view.

Este es el camino que hace alcanzable, por primera vez desde datos reales, el
criterio 4 del spec: una opción con etiquetas distintas por store view acaba
en el espejo como UNA sola `option_id` con varias etiquetas.
"""

import httpx
import pytest
from sqlalchemy import func, select

from skudo.ingest.attribute_sync import sync_attributes
from skudo.ingest.source import TenantSource
from skudo.mirror.attributes import distinct_option_ids, option_labels
from skudo.mirror.models import Attribute, Tenant

# `labels` llega con claves de string porque JSON no tiene otra forma de
# representar un objeto: {"0": "Negro", "1": "Negro PY"} es exactamente lo que
# Task A1 emite, nunca claves int. El ingestor debe convertirlas.
COLOR_PAGE = {
    "items": [
        {
            "code": "color",
            "label": "Color",
            "frontend_input": "select",
            "declared_scope": "global",
            "is_filterable": True,
            "is_required": False,
            "attribute_set_ids": [4, 7],
            "options": [
                {
                    "option_id": 500,
                    "labels": {"0": "Negro", "1": "Negro PY", "3": "Preto BR"},
                }
            ],
        },
        {
            "code": "warranty_zone",
            "label": "Zona de garantía",
            "frontend_input": "text",
            "declared_scope": "website",
            "is_filterable": False,
            "is_required": False,
            "attribute_set_ids": [4],
            "options": [],
        },
    ],
    "next_cursor": None,
}

LABELS_AS_LIST_PAGE = {
    "items": [
        {
            "code": "color",
            "label": "Color",
            "frontend_input": "select",
            "declared_scope": "global",
            "is_filterable": True,
            "is_required": False,
            "attribute_set_ids": [4],
            "options": [
                # Dos claves consecutivas desde 0: exactamente la forma que un
                # `json_encode` de PHP sobre un array plano colapsaría a lista.
                # Si algo aguas arriba de A1 regresionara así, esto debe
                # fallar ruidosamente, no asignar posicionalmente.
                {"option_id": 501, "labels": ["Negro", "Negro PY"]}
            ],
        }
    ],
    "next_cursor": None,
}


def make_source(tenant_id: int, page: dict = COLOR_PAGE) -> TenantSource:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/attributes"):
            cursor = request.url.params.get("cursor")
            if cursor:
                return httpx.Response(200, json={"items": [], "next_cursor": None})
            return httpx.Response(200, json=page)
        return httpx.Response(404)

    return TenantSource(
        tenant_id=tenant_id,
        base_url="https://x.test",
        token="t",
        transport=httpx.MockTransport(handler),
    )


@pytest.fixture
def tenant(db_session):
    row = Tenant(code="nissei", name="Nissei", base_url="https://x.test", token_env_var="T")
    db_session.add(row)
    db_session.flush()
    return row


def test_a_translated_option_ends_up_as_one_option_id_with_two_labels(db_session, tenant):
    """El criterio 4 del spec, de punta a punta: página HTTP simulada ->
    `sync_attributes` -> `distinct_option_ids`/`option_labels`. Sin fixture
    sembrado a mano: todo el dato viene de la página mockeada."""
    sync_attributes(db_session, make_source(tenant.id))

    assert distinct_option_ids(db_session, tenant.id, "color") == [500]
    assert option_labels(db_session, tenant.id, "color", 500) == {
        0: "Negro",
        1: "Negro PY",
        3: "Preto BR",
    }


def test_labels_keys_become_integers_not_strings(db_session, tenant):
    """Si esto no convirtiera las claves, quedarían guardadas como '0', '1', '3'
    y toda búsqueda posterior por store_id entero fallaría en silencio."""
    sync_attributes(db_session, make_source(tenant.id))

    labels = option_labels(db_session, tenant.id, "color", 500)
    assert all(isinstance(store_id, int) for store_id in labels)


def test_attribute_row_is_written_with_every_field(db_session, tenant):
    sync_attributes(db_session, make_source(tenant.id))

    row = db_session.scalar(
        select(Attribute).where(Attribute.tenant_id == tenant.id, Attribute.code == "color")
    )
    assert row.label == "Color"
    assert row.frontend_input == "select"
    assert row.is_filterable is True
    assert row.is_required is False
    assert row.attribute_set_ids == [4, 7]


def test_declared_scope_is_stored_as_given_not_normalized(db_session, tenant):
    """website-scoped no se convierte a booleano ni se remapea: se guarda tal
    cual llega, porque `declared_scope` es texto con tres valores posibles."""
    sync_attributes(db_session, make_source(tenant.id))

    row = db_session.scalar(
        select(Attribute).where(
            Attribute.tenant_id == tenant.id, Attribute.code == "warranty_zone"
        )
    )
    assert row.declared_scope == "website"


def test_running_the_sync_twice_does_not_duplicate_attributes_or_options(db_session, tenant):
    sync_attributes(db_session, make_source(tenant.id))
    sync_attributes(db_session, make_source(tenant.id))

    attribute_count = db_session.scalar(
        select(func.count()).select_from(Attribute).where(Attribute.tenant_id == tenant.id)
    )
    assert attribute_count == 2  # color + warranty_zone, no duplicados
    assert distinct_option_ids(db_session, tenant.id, "color") == [500]


def test_a_second_tenant_never_sees_the_first_tenants_attributes(db_session, tenant):
    other = Tenant(code="other", name="Other", base_url="https://y.test", token_env_var="T2")
    db_session.add(other)
    db_session.flush()

    sync_attributes(db_session, make_source(tenant.id))

    assert distinct_option_ids(db_session, other.id, "color") == []


def test_labels_arriving_as_a_list_raises_instead_of_silently_misassigning(db_session, tenant):
    """Una lista en vez de un objeto es la regresión concreta que Task A1
    documentó: PHP colapsa un mapa entero-clave con claves consecutivas desde
    cero a un array JSON. Debe fallar ruidosamente, no asignar por posición."""
    with pytest.raises(TypeError):
        sync_attributes(db_session, make_source(tenant.id, page=LABELS_AS_LIST_PAGE))
