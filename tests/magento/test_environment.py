import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from skudo.magento.environment import parse_environment

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_opensource_uses_entity_id():
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.product_entity_key == "entity_id"
    assert profile.staging_enabled is False


def test_commerce_with_staging_uses_row_id():
    profile = parse_environment(load("environment_commerce_staging.json"))
    assert profile.product_entity_key == "row_id"
    assert profile.staging_enabled is True


def test_root_category_is_resolved_through_the_store_group():
    """El store view no conoce su root category: la hereda del grupo. Se mueve
    el root del grupo de BR y la store view 3 lo sigue, que es la prueba de que
    la resolución pasa por el grupo y no por un mapa hardcodeado."""
    payload = load("environment_opensource.json")
    payload["store_groups"][1]["root_category_id"] = 47
    profile = parse_environment(payload)
    assert profile.root_category_id_for(1) == 2
    assert profile.root_category_id_for(3) == 47


def test_unknown_store_group_is_an_error():
    """Un grupo colgado de la nada es una sonda inconsistente, no un default."""
    payload = load("environment_opensource.json")
    payload["store_views"][0]["group_id"] = 99
    profile = parse_environment(payload)
    with pytest.raises(KeyError, match="store group"):
        profile.root_category_id_for(1)


def test_unknown_store_view_is_an_error():
    profile = parse_environment(load("environment_opensource.json"))
    with pytest.raises(KeyError):
        profile.store_view(999)


def test_staging_without_row_id_is_rejected_as_inconsistent():
    """Un Magento con Staging tiene que exponer row_id. Si no, la sonda mintió."""
    payload = load("environment_commerce_staging.json")
    payload["product_entity_key"] = "entity_id"
    with pytest.raises(ValueError, match="staging"):
        parse_environment(payload)


def test_an_unmodelled_field_is_rejected_instead_of_swallowed():
    """El propósito entero de este payload es 'nada se asume, todo lo reporta la
    sonda'. Tragarse en silencio un campo no modelado es exactamente la trampa
    que el modelo existe para evitar: el módulo creería haber informado algo que
    el ingestor nunca leyó."""
    payload = load("environment_opensource.json")
    payload["msi_stock_resolver"] = "algo que el ingestor no conoce"
    with pytest.raises(ValidationError, match="msi_stock_resolver"):
        parse_environment(payload)


def test_the_pilot_topology_has_store_views_one_and_three():
    """Topología verificada en la instancia real del tenant piloto: las store
    views son 1 (`py`) y 3 (`br`), no 1 y 2. Un fixture con ids consecutivos
    inventados hace que los tests pasen sobre una tienda que no existe."""
    profile = parse_environment(load("environment_opensource.json"))
    assert sorted(v.id for v in profile.store_views) == [1, 3]
    assert {v.id: v.code for v in profile.store_views} == {1: "py", 3: "br"}


def test_both_pilot_store_views_share_the_same_root_category():
    """PY y BR cuelgan del MISMO árbol (root 2). Consecuencia de diseño: la
    condición de pertenencia al árbol de `derive_category_effect` es idéntica
    para las dos tiendas y no puede discriminar entre ellas."""
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.root_category_id_for(1) == 2
    assert profile.root_category_id_for(3) == 2


def test_the_pilot_store_views_live_in_different_websites():
    """Lo que sí discrimina PY de BR en este tenant es el website."""
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.website_id_for(1) != profile.website_id_for(3)
    assert {w.code for w in profile.websites} == {"base", "website_br"}
