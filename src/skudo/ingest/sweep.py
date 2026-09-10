"""La maquinaria común de barrido: sello de pasada y puerta de pasada completa.

H3 la estableció para `full_sync` (`full_sync_checkpoint` + `_sweep`) y M3 la
generaliza a las dos pasadas que no son por store view —atributos y
categorías—, que hasta entonces documentaban "no hay barrido" como fuera de
alcance. Vive acá y no duplicada en cada ingestor porque la parte que importa
no es el `DELETE`: es la PUERTA que lo precede.

La regla, en una línea: una pasada sella con su generación las filas que toca,
y al terminar borra las que no llevan ese sello — pero sólo si la BASE dice
que la pasada terminó.

Por qué la puerta se lee de la base y no de una variable del llamador: con
commit por página, una pasada interrumpida deja el espejo parcialmente
actualizado —aceptable, lo que había sigue siendo el último hecho observado—
pero un barrido en ese estado borraría todo lo que la pasada no alcanzó a
recorrer. Con la precondición adentro, ese barrido no es una llamada que haya
que acordarse de no hacer: no es expresable.

Y para atributos y categorías la puerta pesa MÁS que para los productos. Un
producto barrido de más vuelve en la próxima sincronización. Una OPCIÓN
barrida de más se lleva sus etiquetas, que son el único registro de que
"Negro" y "Preto" son la misma `option_id` — la identidad que S0 existe para
proteger, y sobre la que decide la consolidación de S1.
"""

from datetime import UTC, datetime

from sqlalchemy import Sequence, select
from sqlalchemy.orm import Session

from skudo.mirror.models import SyncPass

# La MISMA secuencia que usa la pasada de productos. Se comparte a propósito:
# un sello de pasada es un número único con el que marcar filas, y una segunda
# secuencia sería una segunda definición de la misma cosa. Que los valores no
# se repitan entre pasadas es además útil al leer una traza. El nombre viene
# de la migración 0008, cuando la única pasada sellada era la de productos.
SYNC_GENERATION_SEQUENCE = Sequence("product_sync_generation_seq")

# Tipos de pasada de `sync_pass`. La de productos NO está acá: tiene su propia
# tabla (`full_sync_checkpoint`) porque es por store view y además reanudable.
PASS_ATTRIBUTES = "attributes"
PASS_CATEGORIES = "categories"


class IncompletePassSweep(RuntimeError):
    """El barrido se pidió para una pasada que no terminó.

    No es un caso a evitar con cuidado: es el que convierte una interrupción
    inofensiva en la pérdida del espejo. Con commit por página, una pasada
    interrumpida deja el espejo PARCIALMENTE actualizado —correcto y
    esperado—, pero si el barrido corriera igual borraría todo lo que esa
    pasada no llegó a ver, que es casi todo el catálogo. Por eso el barrido
    exige el sello persistido de "pasada completa" y lanza esto si no está,
    en vez de confiar en que ningún camino lo llame antes de tiempo.
    """


def next_generation(session: Session) -> int:
    """Un sello nuevo. Secuencia y no reloj: único sin depender de la
    resolución ni de la monotonía del reloj."""
    return session.scalar(select(SYNC_GENERATION_SEQUENCE.next_value()))


def get_pass(session: Session, tenant_id: int, pass_kind: str) -> SyncPass | None:
    return session.scalar(
        select(SyncPass).where(
            SyncPass.tenant_id == tenant_id, SyncPass.pass_kind == pass_kind
        )
    )


def start_pass(
    session: Session, tenant_id: int, pass_kind: str, generation: int
) -> SyncPass:
    """Deja la fila de esa pasada lista para esta generación, SIN sellar.

    Reinicia siempre, incluso si la pasada anterior había quedado a medias:
    estas pasadas no se reanudan, se repiten completas. Es lo que hace segura
    la repetición — al recorrer todas las páginas desde la primera, todo lo
    que el origen sigue ofreciendo se vuelve a sellar con la generación nueva,
    así que lo que la pasada interrumpida había escrito no queda condenado por
    llevar un sello viejo.
    """
    row = get_pass(session, tenant_id, pass_kind)
    if row is None:
        row = SyncPass(
            tenant_id=tenant_id,
            pass_kind=pass_kind,
            generation=generation,
            pages_done=0,
            items_written=0,
            pass_complete=False,
            swept=False,
        )
        session.add(row)
    else:
        row.generation = generation
        row.pages_done = 0
        row.items_written = 0
        row.pass_complete = False
        row.swept = False
    row.updated_at = datetime.now(UTC)
    session.flush()
    return row


def require_complete_pass(
    session: Session, tenant_id: int, pass_kind: str, generation: int
) -> SyncPass:
    """La puerta. Devuelve la fila de la pasada, o lanza `IncompletePassSweep`.

    Se lee de la BASE y no de una variable del llamador, y las dos condiciones
    cubren los dos modos de fallo:

    - Sin fila, o con otra generación: ninguna fila del espejo lleva ese
      sello, así que el barrido se llevaría TODO lo del tenant.
    - Con la fila de esta generación pero sin `pass_complete`: el barrido se
      llevaría todo lo que la pasada no alcanzó a recorrer.
    """
    row = get_pass(session, tenant_id, pass_kind)
    if row is None or row.generation != generation:
        raise IncompletePassSweep(
            f"no hay pasada de tipo {pass_kind!r} de la generación {generation} para "
            f"el tenant {tenant_id}: no se puede barrer una pasada que esta "
            "generación no registró. Barrer sin ese sello borraría todas las filas "
            "del tenant, porque ninguna lo lleva."
        )
    if not row.pass_complete:
        raise IncompletePassSweep(
            f"la pasada de tipo {pass_kind!r} (generación {generation}, "
            f"{row.pages_done} página(s) aplicada(s)) no llegó a la última página: "
            "barrer ahora borraría todo lo que falta por recorrer. Se aborta; la "
            "próxima pasada vuelve a recorrer desde la primera página y barre "
            "cuando termine."
        )
    return row


def note_page(session: Session, row: SyncPass, items: int, *, is_last: bool) -> None:
    """Anota una página aplicada en la fila de la pasada.

    `pass_complete` se escribe acá, en la MISMA transacción que la página que
    el llamador acaba de aplicar y confirma a continuación: si ese commit no
    llega, la pasada sigue estando a medias y el barrido sigue prohibido.
    """
    row.pages_done += 1
    row.items_written += items
    row.pass_complete = is_last
    row.updated_at = datetime.now(UTC)
    session.flush()
