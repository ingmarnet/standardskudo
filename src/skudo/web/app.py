"""API REST de Skudo.

Expone los datos que el panel necesita: autenticación, tenants, salud,
tendencia, hallazgos. Cada endpoint recibe la sesión de base de datos por
inyección de dependencia de FastAPI, y el usuario autenticado por el token JWT.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from skudo.auth.users import authenticate
from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import findings_report
from skudo.mirror.models import Attribute, AttributeSet, ProductRecord, Tenant
from skudo.rules import curation
from skudo.rules.models import Rule
from skudo.rules.transitions import TransicionInvalida
from skudo.score.models import CatalogScore, ProductScore
from skudo.score.run import tendencia
from skudo.score.trend import comparar_ultima
from skudo.web.auth import create_token, require_role, require_user

# Roles con permiso para curar (aceptar, acotar, rechazar, snapshot). La
# curación es una decisión de gobierno: el lector y el operador no la tocan.
CURATION_ROLES = ("superadmin", "administrador", "aprobador")

app = FastAPI(title="Skudo", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    from skudo.web.deps import get_session_factory

    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


# --- Auth -------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str


@app.post("/api/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
        )
    token = create_token(user.id, user.email, user.role)
    return TokenResponse(access_token=token, role=user.role, email=user.email)


# --- Tenants ----------------------------------------------------------------


@app.get("/api/tenants")
def list_tenants(db: Session = Depends(get_db), user=Depends(require_user)):
    tenants = db.scalars(select(Tenant).order_by(Tenant.code)).all()
    resultado = []
    for t in tenants:
        stores = sorted(
            s
            for (s,) in db.execute(
                select(ProductRecord.store_view_magento_id)
                .where(ProductRecord.tenant_id == t.id)
                .distinct()
            )
        )
        ultima = db.scalars(
            select(CatalogScore)
            .where(CatalogScore.tenant_id == t.id)
            .order_by(CatalogScore.medido_en.desc())
            .limit(1)
        ).first()
        resultado.append(
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "base_url": t.base_url,
                "store_views": stores,
                "ultima_salud": ultima.salud if ultima else None,
                "ultimo_grado": ultima.grado if ultima else None,
                "criticos": ultima.criticos if ultima else None,
                "medido_en": ultima.medido_en.isoformat() if ultima and ultima.medido_en else None,
            }
        )
    return resultado


# --- Salud y tendencia de un tenant -----------------------------------------


@app.get("/api/tenants/{tenant_code}/health")
def tenant_health(
    tenant_code: str,
    store: int | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    stores = sorted(
        s
        for (s,) in db.execute(
            select(ProductRecord.store_view_magento_id)
            .where(ProductRecord.tenant_id == tenant.id)
            .distinct()
        )
    )
    if store is not None:
        stores = [s for s in stores if s == store]

    resultado = {}
    for sv in stores:
        ultima = db.scalars(
            select(CatalogScore)
            .where(
                CatalogScore.tenant_id == tenant.id,
                CatalogScore.store_view_magento_id == sv,
            )
            .order_by(CatalogScore.medido_en.desc())
            .limit(1)
        ).first()
        hist = tendencia(db, tenant.id, sv, limite=30)
        comp = comparar_ultima(db, tenant.id, sv)
        resultado[str(sv)] = {
            "salud": ultima.salud if ultima else None,
            "grado": ultima.grado if ultima else None,
            "productos": ultima.productos if ultima else None,
            "criticos": ultima.criticos if ultima else None,
            "distribucion": ultima.distribucion if ultima else None,
            "por_eje": ultima.por_eje if ultima else None,
            "medido_en": ultima.medido_en.isoformat() if ultima and ultima.medido_en else None,
            "historial": hist,
            "comparacion": comp.resumen() if comp else None,
        }
    return resultado


# --- Hallazgos por store view -----------------------------------------------


@app.get("/api/tenants/{tenant_code}/findings")
def tenant_findings(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    run = db.scalars(
        select(FindingRun)
        .where(
            FindingRun.tenant_id == tenant.id,
            FindingRun.store_view_magento_id == store,
        )
        .order_by(FindingRun.started_at.desc())
        .limit(1)
    ).first()
    if not run:
        return {"hallazgos": [], "cobertura": []}

    report = findings_report(db, run)
    return report




@app.get("/api/tenants/{tenant_code}/findings/{finding_code}")
def finding_details(
    tenant_code: str,
    finding_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    from skudo.findings.models import Finding
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    run = db.scalars(
        select(FindingRun)
        .where(
            FindingRun.tenant_id == tenant.id,
            FindingRun.store_view_magento_id == store,
        )
        .order_by(FindingRun.started_at.desc())
        .limit(1)
    ).first()
    if not run:
        return {"items": []}

    filas = db.scalars(
        select(Finding)
        .where(Finding.run_id == run.id, Finding.code == finding_code)
        .limit(1000)
    ).all()
    
    return {
        "items": [
            {
                "subject_key": f.subject_key,
                "evidence": f.evidence,
                "severity": f.severity
            }
            for f in filas
        ]
    }
# --- Reglas activas por store view ------------------------------------------


@app.get("/api/tenants/{tenant_code}/rules")
def tenant_rules(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    reglas = db.scalars(
        select(Rule)
        .where(
            Rule.tenant_id == tenant.id,
            Rule.status.in_(("aceptada", "aviso", "borrador")),
            (Rule.store_view_magento_id == store) | (Rule.store_view_magento_id.is_(None)),
        )
        .order_by(Rule.id)
    ).all()

    # recuento de productos marcados por regla en la última pasada de esa store view
    ultima = db.scalars(
        select(FindingRun)
        .where(
            FindingRun.tenant_id == tenant.id,
            FindingRun.store_view_magento_id == store,
            FindingRun.finished_at.is_not(None),
        )
        .order_by(FindingRun.id.desc())
        .limit(1)
    ).first()
    marcados: dict[int, int] = {}
    if ultima is not None:
        for rid, n in db.execute(
            select(Finding.rule_id, func.count())
            .where(Finding.run_id == ultima.id, Finding.rule_id.is_not(None))
            .group_by(Finding.rule_id)
        ).all():
            marcados[rid] = n

    return [
        {
            "id": r.id,
            "kind": r.kind,
            "attribute": (r.definition or {}).get("attribute"),
            "scope": f"{r.scope_kind}:{r.scope_key}",
            "axis": r.axis,
            "confidence": r.confidence,
            "evidence_count": r.evidence_count,
            "status": r.status,
            "origin": r.origin,
            "ambiguo": bool((r.definition or {}).get("ambiguo")),
            "definition": r.definition,
            "productos_marcados": marcados.get(r.id, 0),
        }
        for r in reglas
    ]


# --- Curación de reglas desde el panel --------------------------------------
#
# Endpoints de ESCRITURA: envuelven las funciones puras de `skudo.rules.curation`
# y delegan la validez de cada transición en la máquina de estados. La capa web
# sólo agrega control de rol, pertenencia al tenant y el mapeo de las excepciones
# de curación a códigos HTTP. Ninguna re-decide qué transición es válida.


class AceptarRequest(BaseModel):
    confirmar_ambiguo: bool = False


class MotivoRequest(BaseModel):
    motivo: str


def _regla_del_tenant(db: Session, tenant_code: str, rule_id: int) -> Rule:
    """Resuelve una regla comprobando que pertenece al tenant. 404 si no."""
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    regla = db.get(Rule, rule_id)
    if regla is None or regla.tenant_id != tenant.id:
        raise HTTPException(404, "Regla no encontrada en este tenant")
    return regla


def _regla_json(r: Rule) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "attribute": (r.definition or {}).get("attribute"),
        "status": r.status,
        "origin": r.origin,
        "ambiguo": bool((r.definition or {}).get("ambiguo")),
    }


@app.post("/api/tenants/{tenant_code}/rules/{rule_id}/accept")
def accept_rule(
    tenant_code: str,
    rule_id: int,
    body: AceptarRequest = AceptarRequest(),
    db: Session = Depends(get_db),
    user=Depends(require_role(*CURATION_ROLES)),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id)
    try:
        curation.aceptar(
            db, [regla.id], actor=user["email"],
            confirmar_ambiguo=body.confirmar_ambiguo,
        )
    except curation.ReglaAmbiguaSinConfirmar as e:
        raise HTTPException(409, detail={"needs_confirm": True, "message": str(e)})
    except TransicionInvalida as e:
        raise HTTPException(409, detail=str(e))
    db.commit()
    db.refresh(regla)
    return _regla_json(regla)


@app.post("/api/tenants/{tenant_code}/rules/{rule_id}/reject")
def reject_rule(
    tenant_code: str,
    rule_id: int,
    body: MotivoRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*CURATION_ROLES)),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id)
    try:
        curation.rechazar(db, regla.id, actor=user["email"], motivo=body.motivo)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except TransicionInvalida as e:
        raise HTTPException(409, detail=str(e))
    db.commit()
    db.refresh(regla)
    return _regla_json(regla)


@app.post("/api/tenants/{tenant_code}/rules/{rule_id}/limit")
def limit_rule(
    tenant_code: str,
    rule_id: int,
    body: MotivoRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*CURATION_ROLES)),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id)
    try:
        curation.acotar(db, regla.id, actor=user["email"], motivo=body.motivo)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except TransicionInvalida as e:
        raise HTTPException(409, detail=str(e))
    db.commit()
    db.refresh(regla)
    return _regla_json(regla)


@app.post("/api/tenants/{tenant_code}/rules/snapshot")
def snapshot_rules(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_role(*CURATION_ROLES)),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    snap = curation.snapshot(db, tenant_id=tenant.id, store_view=store)
    db.commit()
    return {"version": snap.version, "rule_count": len(snap.rule_ids)}


class CrearReglaRequest(BaseModel):
    kind: str
    attribute: str
    scope_kind: str
    scope_key: str | None = None
    minimo: float | None = None
    maximo: float | None = None


@app.get("/api/tenants/{tenant_code}/rules/opciones")
def rule_options(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Atributos y attribute sets reales del tenant, para los desplegables del
    formulario de nueva regla. El atributo es libre en el panel (datalist), esto
    solo sugiere los que existen."""
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    atributos = [
        {"code": code, "label": label}
        for code, label in db.execute(
            select(Attribute.code, Attribute.label)
            .where(Attribute.tenant_id == tenant.id)
            .order_by(Attribute.code)
        ).all()
    ]
    sets = sorted(
        s
        for (s,) in db.execute(
            select(ProductRecord.attribute_set_id)
            .where(
                ProductRecord.tenant_id == tenant.id,
                ProductRecord.store_view_magento_id == store,
                ProductRecord.attribute_set_id.is_not(None),
            )
            .distinct()
        )
    )
    return {"atributos": atributos, "sets": sets}


@app.get("/api/tenants/{tenant_code}/rules/set-info")
def rule_set_info(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Identifica cada attribute set en uso. El nombre real de Magento aún no se
    sincroniza (tabla `attribute_set` vacía hasta que el módulo lo traiga), así
    que mientras tanto se da una HUELLA: cantidad de productos y los atributos
    más presentes —sin los de sistema—. El nombre se usa apenas exista."""
    from skudo.rules.inference import _es_atributo_de_sistema

    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    nombres = {
        mid: name
        for mid, name in db.execute(
            select(AttributeSet.magento_id, AttributeSet.name).where(
                AttributeSet.tenant_id == tenant.id
            )
        ).all()
    }

    # Una pasada acotada sobre el espejo: conteo por set y frecuencia de cada
    # atributo distintivo. Se limita el escaneo para que sea barato por request.
    conteo: dict[int, int] = {}
    frec: dict[int, dict[str, int]] = {}
    filas = db.execute(
        select(ProductRecord.attribute_set_id, ProductRecord.attributes)
        .where(
            ProductRecord.tenant_id == tenant.id,
            ProductRecord.store_view_magento_id == store,
            ProductRecord.attribute_set_id.is_not(None),
        )
        .order_by(ProductRecord.sku)
        .limit(4000)
    ).all()
    for sid, attrs in filas:
        conteo[sid] = conteo.get(sid, 0) + 1
        f = frec.setdefault(sid, {})
        for code, val in (attrs or {}).items():
            if _es_atributo_de_sistema(code):
                continue
            if val is None or str(val).strip() == "":
                continue
            f[code] = f.get(code, 0) + 1

    info = {}
    for sid, n in conteo.items():
        top = sorted(frec.get(sid, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        info[str(sid)] = {
            "name": nombres.get(sid),
            "productos": n,
            "atributos": [code for code, _ in top],
        }
    return info


@app.get("/api/tenants/{tenant_code}/catalog-design")
def catalog_design(
    tenant_code: str,
    store: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """Eje 11 — diseño del catálogo: sets muertos + filtros mal puestos. Es
    análisis de CONFIGURACIÓN (no de producto): no toca la nota. Los filtros
    salen del último perfil terminado; sin perfil, sólo los sets muertos."""
    from skudo.findings import catalog_design as cd
    from skudo.profile.models import ProfileRun

    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    perfil = db.scalars(
        select(ProfileRun)
        .where(
            ProfileRun.tenant_id == tenant.id,
            ProfileRun.store_view_magento_id == store,
            ProfileRun.finished_at.is_not(None),
        )
        .order_by(ProfileRun.id.desc())
        .limit(1)
    ).first()

    return {
        "sets_muertos": cd.sets_muertos(db, tenant.id),
        "filtros_inutiles": cd.filtros_inutiles(db, tenant.id, perfil) if perfil else [],
        "filtros_perdidos": cd.filtros_perdidos(db, tenant.id, perfil) if perfil else [],
        "perfil": perfil.id if perfil else None,
    }


@app.post("/api/tenants/{tenant_code}/rules")
def create_rule(
    tenant_code: str,
    store: int,
    body: CrearReglaRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*CURATION_ROLES)),
):
    """Crea una regla `curada` en borrador desde el panel. Entra al mismo
    circuito de curación que las inferidas."""
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    try:
        regla = curation.crear(
            db, tenant_id=tenant.id, store_view_magento_id=store,
            scope_kind=body.scope_kind, scope_key=body.scope_key, kind=body.kind,
            attribute=body.attribute, actor=user["email"],
            minimo=body.minimo, maximo=body.maximo,
        )
    except curation.ReglaDuplicada as e:
        raise HTTPException(409, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    db.commit()
    db.refresh(regla)
    return _regla_json(regla)


# --- Productos con peor nota ------------------------------------------------


@app.get("/api/tenants/{tenant_code}/products")
def tenant_products(
    tenant_code: str,
    store: int,
    grado: str | None = None,
    critico: bool | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")

    q = (
        select(ProductScore)
        .where(
            ProductScore.tenant_id == tenant.id,
            ProductScore.store_view_magento_id == store,
        )
    )
    if grado:
        q = q.where(ProductScore.grado == grado.upper())
    if critico is not None:
        q = q.where(ProductScore.critico == critico)

    total = db.scalar(
        select(func.count())
        .select_from(ProductScore)
        .where(
            ProductScore.tenant_id == tenant.id,
            ProductScore.store_view_magento_id == store,
            *([ProductScore.grado == grado.upper()] if grado else []),
            *([ProductScore.critico == critico] if critico is not None else []),
        )
    )

    # pleno antes que sin_stock; NULL (sin datos de stock) al final. Dentro de
    # cada grupo se mantiene el orden por peor nota.
    orden_prioridad = case({"pleno": 0, "sin_stock": 1}, value=ProductScore.prioridad_vitrina, else_=2)
    filas = db.scalars(
        q.order_by(orden_prioridad, ProductScore.puntaje, ProductScore.sku)
        .limit(min(limit, 200))
        .offset(offset)
    ).all()

    return {
        "total": total,
        "productos": [
            {
                "sku": p.sku,
                "puntaje": p.puntaje,
                "grado": p.grado,
                "critico": p.critico,
                "deducciones": p.deducciones,
                "prioridad_vitrina": p.prioridad_vitrina,
            }
            for p in filas
        ],
    }


# --- Panel (SPA) ------------------------------------------------------------

_STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html", media_type="text/html")
