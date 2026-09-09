"""Política de fechas ilegibles del origen.

`strptime` revienta con la cadena vacía y con el `0000-00-00 00:00:00` que
MySQL admite y los catálogos Magento heredados sí contienen. Una fila así
abortaba la página entera y, en `delta_sync`, impedía que avanzara el
watermark: una píldora envenenada que bloqueaba toda sincronización posterior.

La política explícita es: una fecha que no se puede interpretar es DESCONOCIDA.
Se guarda NULL —no una fecha de relleno, que sería un dato falso con aspecto
confiable— y el caso se reporta para que nadie lo descubra por casualidad.
"""

from datetime import UTC, datetime

import pytest

from skudo.ingest.full_sync import parse_magento_datetime


def test_a_well_formed_date_is_parsed_as_utc():
    assert parse_magento_datetime("2026-09-01 10:00:00") == datetime(
        2026, 9, 1, 10, 0, 0, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "0000-00-00 00:00:00",
        "0000-00-00",
        "no es una fecha",
        "2026-13-45 99:99:99",
        None,
    ],
)
def test_an_unreadable_date_is_unknown_instead_of_an_exception(raw):
    assert parse_magento_datetime(raw) is None
