"""El informe HTML, generado desde los hallazgos guardados.

Todo el texto que entra viene del catálogo de un cliente —nombres de producto,
SKUs— y sale a un archivo HTML que alguien abre en un navegador. Así que **todo
se escapa**, sin excepción y sin confiar en que un nombre de producto sea
inofensivo: un `<` en un nombre rompería la página, y algo peor la convertiría
en un problema de seguridad del que abre el informe.
"""

from datetime import UTC, datetime
from html import escape

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.findings.models import DetectorCoverage, Finding, FindingRun
from skudo.report.textos import texto_de

ORDEN_SEVERIDAD = {"alta": 0, "media": 1, "baja": 2, "candidato": 3, "aviso": 4}
CLASE = {"alta": "alta", "media": "media", "baja": "baja", "candidato": "media", "aviso": "media"}
ETIQUETA = {
    "alta": "Impacto alto",
    "media": "Impacto medio",
    "baja": "Impacto acotado",
    "candidato": "Candidato a revisar",
    "aviso": "Decisión, no error",
}

EJEMPLOS_POR_HALLAZGO = 3


def mil(n: int) -> str:
    """Separador de miles con punto, como se escribe acá.

    Una función y no un `replace` sobre el documento entero: ese atajo cambiaba
    también las comas del CSS (`Georgia,serif`) y las de los nombres de producto
    (el talle `5,5'` pasaba a `5.5'`). El formato de un número se aplica al
    número, no a la página.
    """
    return f"{n:,}".replace(",", ".")


def _datos(session: Session, run: FindingRun) -> list[dict]:
    coberturas = {
        c.detector: c
        for c in session.scalars(
            select(DetectorCoverage).where(DetectorCoverage.run_id == run.id)
        )
    }
    de_detector = {
        "variantes_sueltas": "nombres_repetidos",
        "nombre_repetido": "nombres_repetidos",
    }
    grupos = session.execute(
        select(Finding.code, Finding.severity, func.count())
        .where(Finding.run_id == run.id)
        .group_by(Finding.code, Finding.severity)
    ).all()

    salida = []
    for code, severity, n in grupos:
        cobertura = coberturas.get(de_detector.get(code, code))
        ejemplos = session.scalars(
            select(Finding)
            .where(Finding.run_id == run.id, Finding.code == code)
            .order_by(Finding.subject_key)
            .limit(EJEMPLOS_POR_HALLAZGO)
        ).all()
        salida.append(
            {
                "code": code,
                "severity": severity,
                "n": n,
                "cobertura": cobertura,
                "ejemplos": ejemplos,
                "texto": texto_de(code),
            }
        )
    salida.sort(key=lambda d: (ORDEN_SEVERIDAD.get(d["severity"], 9), -d["n"]))
    return salida


def _ejemplo_html(f: Finding) -> str:
    if f.subject_type == "grupo":
        ev = f.evidence or {}
        extra = ""
        if ev.get("talles"):
            extra = "<br>talles " + escape(" · ".join(str(t) for t in ev["talles"]))
        return (
            f"<li><strong>{escape(str(ev.get('productos', '?')))} fichas</strong> · "
            f"{escape(f.subject_key)}{extra}</li>"
        )
    return f'<li><span class="sku">{escape(f.subject_key)}</span></li>'


def render(session: Session, run: FindingRun, *, tienda: str) -> str:
    datos = _datos(session, run)
    totales = sum(d["n"] for d in datos)
    evaluados = max((d["cobertura"].evaluados for d in datos if d["cobertura"]), default=0)
    no_aplica = max((d["cobertura"].no_aplica for d in datos if d["cobertura"]), default=0)
    fecha = (run.finished_at or datetime.now(UTC)).strftime("%d/%m/%Y")

    bloques = []
    for d in datos:
        t, c = d["texto"], d["cobertura"]
        titulo = t.encabezado(d["n"], mil)
        pct = f"{100 * d['n'] / c.evaluados:.1f} %" if c and c.evaluados else "—"
        ejemplos = "".join(_ejemplo_html(f) for f in d["ejemplos"])
        bloques.append(f"""
<div class="finding {CLASE.get(d['severity'], 'media')}">
  <header>
    <h3>{escape(titulo)}</h3>
    <span class="chip {CLASE.get(d['severity'], 'media')}">{ETIQUETA.get(d['severity'], '')}</span>
  </header>
  <p>{escape(t.significa)}</p>
  <p class="medicion">Medido sobre <strong>{c.evaluados if c else '?'}</strong> productos
     evaluables — {escape(pct)} del total.
     {("No se evaluaron " + str(c.no_aplica) + ": " + escape(c.motivo_no_aplica) + ".") if c and c.no_aplica else ""}
     {("<strong>" + str(c.no_evaluado) + " no se pudieron mirar.</strong>") if c and c.no_evaluado else ""}</p>
  <div class="ejemplos"><span class="t">Ejemplos</span><ul>{ejemplos}</ul></div>
  <p class="accion"><b>Qué hacer:</b> {escape(t.accion)}</p>
</div>""")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diagnóstico de catálogo · {escape(tienda)}</title>
<style>
:root {{ --bg:#F6F5F2; --card:#fff; --sunk:#EDEBE6; --ink:#1A1814; --ink-2:#4E4A43;
  --ink-3:#7A746A; --line:#DFDCD4; --line-2:#C2BDB2; --accent:#2B4A7D;
  --alta:#A8452F; --alta-bg:#F7E7E2; --media:#8A6209; --media-bg:#F8EEDB;
  --baja:#46654A; --baja-bg:#E7EFE7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14120F; --card:#1C1A16;
  --sunk:#23201B; --ink:#EDE9E2; --ink-2:#B3ACA0; --ink-3:#8A8377; --line:#2C2823;
  --line-2:#403A32; --accent:#8FB0DF; --alta:#E0897A; --alta-bg:#2C1B17;
  --media:#D6A947; --media-bg:#2A2314; --baja:#8FB795; --baja-bg:#1A241B; }} }}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font:1.0625rem/1.65 Georgia,serif;
  margin:0;padding:0 clamp(1rem,4vw,3rem) 4rem}}
.wrap{{max-width:54rem;margin:0 auto}}
h1,h2,h3,.chip,.stat .v,.accion,.medicion,th{{font-family:system-ui,-apple-system,sans-serif}}
h1{{font-size:2.25rem;line-height:1.08;letter-spacing:-.02em;margin:2.5rem 0 1rem}}
h2{{font-size:1.5rem;margin:2.5rem 0 .75rem}}
.eyebrow{{font:600 .75rem/1 system-ui;letter-spacing:.16em;text-transform:uppercase;
  color:var(--accent);margin:0}}
.lede{{font-size:1.25rem;color:var(--ink-2);max-width:64ch}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:1.25rem 2rem;
  padding:1.35rem 0;border-top:2px solid var(--ink);border-bottom:1px solid var(--line-2)}}
.stat .v{{display:block;font-size:3rem;font-weight:700;line-height:.95;
  letter-spacing:-.03em;font-variant-numeric:tabular-nums}}
.stat .k{{display:block;font:.875rem/1.35 system-ui;color:var(--ink-2);margin-top:.4rem}}
p{{max-width:64ch}}
.finding{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--line-2);
  border-radius:3px;padding:1.15rem 1.25rem;margin:1.1rem 0}}
.finding.alta{{border-left-color:var(--alta)}} .finding.media{{border-left-color:var(--media)}}
.finding.baja{{border-left-color:var(--baja)}}
.finding header{{display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap;margin-bottom:.5rem}}
.finding h3{{margin:0;font-size:1.25rem;font-weight:700;flex:1 1 15rem}}
.chip{{font:600 .75rem/1 system-ui;letter-spacing:.08em;text-transform:uppercase;
  padding:.28rem .5rem;border-radius:2px;white-space:nowrap}}
.chip.alta{{color:var(--alta);background:var(--alta-bg)}}
.chip.media{{color:var(--media);background:var(--media-bg)}}
.chip.baja{{color:var(--baja);background:var(--baja-bg)}}
.medicion{{font-size:.875rem;color:var(--ink-3);max-width:none}}
.ejemplos{{background:var(--sunk);border-radius:3px;padding:.7rem .85rem;margin:.8rem 0;
  font-size:.875rem}}
.ejemplos .t{{display:block;font:600 .75rem/1 system-ui;letter-spacing:.1em;
  text-transform:uppercase;color:var(--ink-3);margin-bottom:.4rem}}
.ejemplos ul{{list-style:none;padding:0;margin:0}} .ejemplos li{{margin-bottom:.25rem}}
.sku{{font-family:ui-monospace,monospace;font-size:.85em;background:var(--bg);
  padding:.08em .4em;border-radius:2px}}
.accion{{font-size:.875rem;border-top:1px solid var(--line);padding-top:.65rem;
  margin:.85rem 0 0;color:var(--ink-2);max-width:none}}
footer{{margin-top:3rem;padding-top:1.25rem;border-top:2px solid var(--ink);
  font-size:.875rem;color:var(--ink-3)}}
</style></head><body><div class="wrap">
<p class="eyebrow">Diagnóstico de calidad de catálogo · Skudo</p>
<h1>Qué le falta al catálogo de {escape(tienda)}</h1>
<p class="lede">Análisis del {fecha}. {mil(run.product_count)} registros leídos y verificados
uno por uno contra la tienda.</p>
<div class="stats">
  <div class="stat"><span class="v">{mil(evaluados)}</span><span class="k">productos publicados,
    sobre {mil(run.product_count)} registros</span></div>
  <div class="stat"><span class="v">{mil(totales)}</span><span class="k">hallazgos en total</span></div>
  <div class="stat"><span class="v">{mil(no_aplica)}</span><span class="k">registros excluidos
    por no corresponder</span></div>
</div>
<h2>Por qué el denominador no es {mil(run.product_count)}</h2>
<p>El catálogo tiene {mil(run.product_count)} registros, pero medir contra ese número exagera
todo: buena parte son variantes que nadie navega y productos deshabilitados. Cada hallazgo
de este informe dice <strong>sobre cuántos productos se buscó</strong> y a cuántos no les
correspondía la pregunta.</p>
<h2>Hallazgos, por lo que cuestan</h2>
{"".join(bloques)}
<footer><p>Generado por Skudo el {fecha} sobre {escape(tienda)}. Ninguna cifra es una
estimación: el espejo del catálogo se verifica producto por producto contra la tienda antes
de medir, y cada número viaja con la cobertura del detector que lo produjo.</p></footer>
</div></body></html>"""
