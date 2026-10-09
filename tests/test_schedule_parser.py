"""Pruebas del parser de recordatorios."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services.schedule_parser import ReminderError, parse_reminder

TZ = ZoneInfo("America/Lima")
NOW = datetime(2026, 10, 9, 20, 0, tzinfo=TZ)


@pytest.mark.parametrize(
    ("entrada", "kind", "descripcion", "texto"),
    [
        ("30m Tomar agua", "once", "en 30m", "Tomar agua"),
        ("1h30m Reunión", "once", "en 1h30m", "Reunión"),
        ("18:30 Llamar", "once", "mañana a las 18:30", "Llamar"),
        ("mañana 8:00 Sacar basura", "once", "mañana a las 08:00", "Sacar basura"),
        ("pasado mañana 9:00 X", "once", "el domingo 11/10 a las 09:00", "X"),
        ("lunes 9:00 Reporte", "once", "el lunes 12/10 a las 09:00", "Reporte"),
        ("25/12 10:00 Felicitar", "once", "el viernes 25/12 a las 10:00", "Felicitar"),
        ("cada día 8:00 Vitaminas", "daily", "cada día a las 08:00", "Vitaminas"),
        ("todos los días 7:30 Y", "daily", "cada día a las 07:30", "Y"),
        ("diario 8:00 Z", "daily", "cada día a las 08:00", "Z"),
        ("cada lunes 9:00 Reporte", "weekly", "cada lunes a las 09:00", "Reporte"),
        ("los viernes 20:00 W", "weekly", "cada viernes a las 20:00", "W"),
        ("cada 2h Agua", "interval", "cada 2h", "Agua"),
        ("8pm Cena", "once", "mañana a las 20:00", "Cena"),
    ],
)
def test_parse_reminder(entrada, kind, descripcion, texto):
    schedule, parsed_text = parse_reminder(entrada, TZ, now=NOW)
    assert schedule.kind == kind
    assert schedule.description == descripcion
    assert parsed_text == texto


def test_sin_zona_horaria_las_horas_fallan():
    with pytest.raises(ReminderError, match="zona horaria"):
        parse_reminder("18:30 Llamar", None, now=NOW)


def test_sin_zona_horaria_los_relativos_funcionan():
    schedule, _ = parse_reminder("30m Agua", None, now=NOW)
    assert schedule.kind == "once"


@pytest.mark.parametrize("entrada", ["cada 1m Agua", "30m", "mañana 8:00", "25/13 10:00 X"])
def test_errores_amigables(entrada):
    with pytest.raises(ReminderError):
        parse_reminder(entrada, TZ, now=NOW)
