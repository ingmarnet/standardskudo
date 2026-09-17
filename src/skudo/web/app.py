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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skudo.auth.users import authenticate
from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import findings_report
from skudo.mirror.models import ProductRecord, Tenant
from skudo.rules.models import Rule
from skudo.score.models import CatalogScore, ProductScore
from skudo.score.run import tendencia
from skudo.score.trend import comparar_ultima
from skudo.web.auth import create_token, require_user

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
            "productos_marcados": marcados.get(r.id, 0),
        }
        for r in reglas
    ]


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

    filas = db.scalars(
        q.order_by(ProductScore.puntaje, ProductScore.sku)
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
            }
            for p in filas
        ],
    }


# --- Panel (SPA) ------------------------------------------------------------

_STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html", media_type="text/html")
