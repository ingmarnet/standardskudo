"""Pasada de nombres de attribute set: `/attribute-sets` → espejo.

Recibe un `TenantSource` (no `(client, tenant_id)` sueltos) por la misma razón
que las otras pasadas: para que el catálogo que se lee y el tenant en el que se
escribe no puedan desemparejarse.
"""

from sqlalchemy.orm import Session

from skudo.ingest.source import TenantSource
from skudo.mirror.attribute_sets import upsert_attribute_sets


def sync_attribute_sets(session: Session, source: TenantSource) -> int:
    """Vuelca los nombres de attribute set en el espejo. Devuelve cuántos."""
    rows = source.client.get_attribute_sets()
    n = upsert_attribute_sets(session, source.tenant_id, rows)
    session.commit()
    return n
