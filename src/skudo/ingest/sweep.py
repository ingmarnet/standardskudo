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

# La válvula del barrido masivo. Un barrido que se llevaría MÁS de esta
# proporción de las filas que tiene a su alcance se niega y pide autorización
# explícita.
#
# Por qué 0,5 —la mayoría estricta— y no otro número: por debajo de la mitad,
# un borrado grande es indistinguible de la rotación normal de un catálogo, y
# una válvula que dispara en las pasadas normales es una válvula que alguien
# apaga. Por encima de la mitad, el barrido está afirmando algo sobre el
# catálogo ENTERO del tenant, que es una afirmación que merece un humano. Y el
# fallo que motiva todo esto —un endpoint que responde `[]`, un token vencido
# que igual da 200, una paginación rota— produce siempre el 100 %, cómodamente
# del lado que se niega.
#
# La comparación es ESTRICTA (`>`): exactamente la mitad pasa. "La mayoría se
# va" y "la mitad cambió" son cosas distintas, y el segundo caso es rotación.
MASS_SWEEP_MAX_SHARE = 0.5

# Piso de filas por debajo del cual la válvula no se aplica. No es una
# concesión: es que por debajo de una decena de filas la válvula no puede
# proteger nada que importe —resincronizar eso cuesta segundos— y a cambio
# haría que cualquier espejo recién nacido o cualquier tenant de juguete
# necesite una bandera para funcionar. La protección empieza donde hay algo
# que proteger.
MASS_SWEEP_MIN_ROWS = 10

# Cómo se dice que sí, en el único lugar donde se dice. El mensaje de la
# negativa lo nombra para que quien lo lee a las tres de la mañana no tenga
# que buscarlo.
SWEEP_OVERRIDE_FLAG = "--sweep-anyway"


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


class MassSweepRefused(RuntimeError):
    """El barrido se llevaría la mayoría de las filas a su alcance.

    Es el hueco que el cierre de M3 dejó declarado: para el sello, una pasada
    completa que no devolvió NADA es indistinguible de "el origen dejó de
    ofrecer todo el catálogo". La pasada termina, marca `pass_complete`, y el
    barrido borra el espejo entero del tenant — con las etiquetas de las
    opciones adentro, que son el único registro de que "Negro" y "Preto" son
    la misma `option_id`.

    El espejo es derivado y una resincronización lo reconstruye, así que el
    daño está acotado. Lo inaceptable es que sea SILENCIOSO: un sistema cuya
    premisa entera es no destruir valor no puede tener un camino destructivo
    que dispara con más fuerza justo cuando algo aguas arriba se rompió.

    Por eso es un ABORTO con los conteos en el mensaje y no un salto
    silencioso: un barrido que "no hizo nada" sin decirlo dejaría el espejo
    con filas muertas y a nadie enterado. Y por eso la salida es una bandera
    explícita y no un número que se pueda bajar: un umbral configurable en un
    cron termina configurado en 1,0 y la válvula deja de existir sin que nadie
    lo decida.
    """


def guard_mass_sweep(
    session: Session,
    *,
    what: str,
    total: int,
    to_delete: int,
    override: bool,
) -> None:
    """Se niega si el barrido se llevaría más que la mayoría de `what`.

    `total` es lo que ESE barrido tiene a su alcance —las filas del tenant en
    esa store view, o en esa tabla—, nunca el espejo entero: medir la
    proporción sobre el total del tenant haría que vaciar PY con BR intacto
    diera 50 % y pasara inadvertido.

    No borra ni escribe nada: se llama ANTES de la primera sentencia de
    borrado, así que una negativa deja el espejo exactamente como estaba.
    """
    if override or to_delete <= MASS_SWEEP_MIN_ROWS:
        return
    if to_delete <= total * MASS_SWEEP_MAX_SHARE:
        return
    share = to_delete / total if total else 1.0
    raise MassSweepRefused(
        f"el barrido borraría {to_delete} de {total} fila(s) de {what} "
        f"({share:.1%}), más de la mayoría. Se aborta SIN tocar el espejo: una "
        "pasada completa que no devuelve nada —un endpoint que responde vacío, "
        "un token vencido que igual da 200, una paginación rota— es "
        "indistinguible, para el sello, de un catálogo que se vació de verdad. "
        "Verificá el origen; si el vaciado es real, repetí con "
        f"{SWEEP_OVERRIDE_FLAG}."
    )


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
