"""Lectura de configuración de tienda del espejo."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from skudo.mirror.models import StoreSetting

_VERDADEROS = {"true", "1", "yes", "si"}
_FALSOS = {"false", "0", "no"}


def muestra_sin_stock(session: Session, tenant_id: int, store_view_magento_id: int) -> bool | None:
    """¿Magento muestra los productos sin stock en esta store view?

    `None` cuando la config no está sincronizada: es 'no sé', no 'no'. El motor
    lo trata como ignorancia y no oculta a nadie (ver Global Constraints).
    """
    valor = session.scalar(
        select(StoreSetting.value).where(
            StoreSetting.tenant_id == tenant_id,
            StoreSetting.store_view_magento_id == store_view_magento_id,
            StoreSetting.key == "show_out_of_stock",
        )
    )
    if valor is None:
        return None
    v = valor.strip().lower()
    if v in _VERDADEROS:
        return True
    if v in _FALSOS:
        return False
    return None
