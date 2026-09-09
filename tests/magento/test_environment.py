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
    """El store view no conoce su root category: la hereda del grupo."""
    profile = parse_environment(load("environment_opensource.json"))
    assert profile.root_category_id_for(1) == 2
    assert profile.root_category_id_for(2) == 3


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
