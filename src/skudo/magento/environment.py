from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class Website(BaseModel):
    id: int
    code: str
    name: str


class StoreGroup(BaseModel):
    id: int
    website_id: int
    code: str
    name: str
    root_category_id: int


class StoreView(BaseModel):
    id: int
    group_id: int
    code: str
    name: str
    is_active: bool
    locale: str
    currency: str


class EnvironmentProfile(BaseModel):
    """Lo que el sistema descubrió del Magento del tenant.

    Nada aquí se asume: todo lo reporta la sonda del módulo. De ahí
    `extra="forbid"`: es el único payload cuyo propósito entero es que cada
    campo venga de la sonda, así que un campo no modelado significa que el
    módulo informa algo que el ingestor no lee. Aceptarlo en silencio dejaría
    creer que ese dato ya viaja; fallar ruidosamente obliga a modelarlo.
    """

    model_config = ConfigDict(extra="forbid")

    edition: str
    version: str
    product_entity_key: Literal["entity_id", "row_id"]
    staging_enabled: bool
    msi_enabled: bool
    default_stock_id: int | None
    websites: list[Website]
    store_groups: list[StoreGroup]
    store_views: list[StoreView]
    counts: dict[str, int]
    module_version: str

    @model_validator(mode="after")
    def staging_implies_row_id(self):
        if self.staging_enabled and self.product_entity_key != "row_id":
            raise ValueError(
                "staging activo pero la clave de entidad es entity_id: "
                "la sonda es inconsistente y no se puede confiar en ella"
            )
        return self

    def store_view(self, store_id: int) -> StoreView:
        for view in self.store_views:
            if view.id == store_id:
                return view
        raise KeyError(f"store view desconocida: {store_id}")

    def root_category_id_for(self, store_id: int) -> int:
        group_id = self.store_view(store_id).group_id
        for group in self.store_groups:
            if group.id == group_id:
                return group.root_category_id
        raise KeyError(f"store group desconocido: {group_id}")


def parse_environment(payload: dict) -> EnvironmentProfile:
    return EnvironmentProfile.model_validate(payload)
