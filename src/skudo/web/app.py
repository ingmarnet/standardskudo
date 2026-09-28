"""API REST de Skudo.

Expone los datos que el panel necesita: autenticación, tenants, salud,
tendencia, hallazgos. Cada endpoint recibe la sesión de base de datos por
inyección de dependencia de FastAPI, y el usuario autenticado por el token JWT.
"""

import re
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from skudo.audit.log import record
from skudo.audit.models import AuditLog
from skudo.auth.models import ROLES, PlatformUser
from skudo.auth.passwords import hash_password
from skudo.auth.users import (
    authenticate,
    set_tenant_access,
    tenant_role_for,
    tenant_roles_for_user,
)
from skudo.auth.users import (
    create_user as auth_create_user,
)
from skudo.auth.users import (
    list_users as auth_list_users,
)
from skudo.config import default_token_env_var, write_tenant_token
from skudo.evidence.service import conflicts_for_subject, list_for_subject
from skudo.findings.models import Finding, FindingRun
from skudo.findings.run import findings_report
from skudo.mirror.models import Attribute, AttributeSet, ProductRecord, ProductSignal, Tenant
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
ADMIN_ROLES = ("superadmin", "administrador")

app = FastAPI(title="Skudo", version="0.1.0")



def _is_admin(user: dict) -> bool:
    return user.get("role") in ADMIN_ROLES


def _explicit_tenant_ids(db: Session, user: dict) -> list[int]:
    try:
        user_id = int(user.get("sub"))
    except (TypeError, ValueError):
        return []
    return [r["tenant_id"] for r in tenant_roles_for_user(db, user_id)]


def _check_tenant_access(db: Session, tenant: Tenant, user: dict) -> None:
    """Aplica alcance por tenant sin romper cuentas legadas.

    Administradores ven todo. Para roles no administradores, la primera fila en
    `platform_user_tenant_access` activa el modo restrictivo: sólo ven esos
    tenants. Sin filas, se conserva el acceso legado a todos los tenants para no
    bloquear usuarios existentes durante el rollout.
    """
    if _is_admin(user):
        return
    allowed = _explicit_tenant_ids(db, user)
    if allowed and tenant.id not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="usuario sin acceso a este tenant",
        )


def _effective_role(db: Session, tenant: Tenant, user: dict) -> str:
    """Rol efectivo del usuario sobre un tenant.

    Administradores mandan en todos lados. Para el resto, la fila de acceso por
    tenant gana si existe; si no, manda el rol global (compatibilidad legada).
    """
    global_role = user.get("role", "lector")
    if global_role in ADMIN_ROLES:
        return global_role
    try:
        user_id = int(user.get("sub"))
    except (TypeError, ValueError):
        return global_role
    per_tenant = tenant_role_for(db, user_id, tenant.id)
    return per_tenant if per_tenant else global_role


def _require_tenant_role(db: Session, tenant: Tenant, user: dict, *roles: str) -> str:
    """Exige uno de `roles` como rol EFECTIVO sobre el tenant. 403 si no."""
    role = _effective_role(db, tenant, user)
    if role not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="rol sin permiso para esta acción en este tenant",
        )
    return role


def _user_json(db: Session, user: PlatformUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "tenant_roles": tenant_roles_for_user(db, user.id),
    }

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



# --- Usuarios y permisos -----------------------------------------------------


class TenantRoleInput(BaseModel):
    tenant_id: int
    role: str


class UserCreateRequest(BaseModel):
    email: str
    password: str
    role: str
    tenant_roles: list[TenantRoleInput] = []


class UserUpdateRequest(BaseModel):
    role: str | None = None
    password: str | None = None
    is_active: bool | None = None
    tenant_roles: list[TenantRoleInput] | None = None


@app.get("/api/users")
def list_platform_users(
    db: Session = Depends(get_db),
    user=Depends(require_role(*ADMIN_ROLES)),
):
    return [_user_json(db, u) for u in auth_list_users(db)]


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_platform_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*ADMIN_ROLES)),
):
    try:
        new_user = auth_create_user(db, body.email, body.password, body.role)
        set_tenant_access(db, new_user, [r.model_dump() for r in body.tenant_roles])
        record(db, action="user.create", actor_email=user["email"], entity_type="user", entity_id=new_user.id, detail={"role": body.role})
        db.commit()
        db.refresh(new_user)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, detail=str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, detail="email ya registrado")
    return _user_json(db, new_user)


@app.patch("/api/users/{user_id}")
def update_platform_user(
    user_id: int,
    body: UserUpdateRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*ADMIN_ROLES)),
):
    target = db.get(PlatformUser, user_id)
    if target is None:
        raise HTTPException(404, "Usuario no encontrado")

    try:
        if body.role is not None:
            if body.role not in ROLES:
                raise ValueError(
                    f"rol desconocido: {body.role!r}. Los válidos son {', '.join(ROLES)}"
                )
            target.role = body.role
        if body.password is not None:
            target.password_hash = hash_password(body.password)
        if body.is_active is not None:
            target.is_active = body.is_active
        if body.tenant_roles is not None:
            set_tenant_access(db, target, [r.model_dump() for r in body.tenant_roles])
        record(db, action="user.update", actor_email=user["email"], entity_type="user", entity_id=target.id)
        db.commit()
        db.refresh(target)
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, detail=str(e))
    return _user_json(db, target)


# --- Tenants ----------------------------------------------------------------


class TenantCreateRequest(BaseModel):
    code: str
    name: str | None = None
    base_url: str


@app.post("/api/tenants", status_code=status.HTTP_201_CREATED)
def create_tenant(
    body: TenantCreateRequest,
    db: Session = Depends(get_db),
    user=Depends(require_role(*ADMIN_ROLES)),
):
    """Onboarding autoservicio: crea el tenant y devuelve su token UNA vez.

    El token no se guarda en la base (seguimos el invariante de secretos): se
    genera acá, se devuelve al administrador una sola vez, y es él quien lo
    instala en el Magento del cliente y en la variable de entorno que
    `token_env_var` nombra en el servidor.
    """
    code = body.code.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", code):
        raise HTTPException(400, "código inválido: solo minúsculas, dígitos, guion o guion bajo")
    base_url = body.base_url.strip()
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url debe empezar con http:// o https://")
    if db.scalar(select(Tenant.id).where(Tenant.code == code)) is not None:
        raise HTTPException(409, "ya existe un tenant con ese código")

    token = secrets.token_urlsafe(32)
    write_tenant_token(code, token)  # persiste en disco, nunca en la base
    tenant = Tenant(
        code=code,
        name=(body.name or "").strip() or code,
        base_url=base_url,
        token_env_var=default_token_env_var(code),
    )
    db.add(tenant)
    db.flush()
    record(db, action="tenant.create", actor_email=user["email"], tenant_id=tenant.id, entity_type="tenant", entity_id=tenant.id, detail={"code": code})
    db.commit()
    db.refresh(tenant)
    return {
        "id": tenant.id,
        "code": tenant.code,
        "name": tenant.name,
        "base_url": tenant.base_url,
        "token_env_var": tenant.token_env_var,
        "token": token,
    }


@app.get("/api/audit")
def audit_log(
    tenant_code: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    user=Depends(require_role(*ADMIN_ROLES)),
):
    """Bitácora inmutable de escrituras. Solo administradores de plataforma."""
    q = select(AuditLog)
    if tenant_code:
        tid = db.scalar(select(Tenant.id).where(Tenant.code == tenant_code))
        if tid is None:
            raise HTTPException(404, "Tenant no encontrado")
        q = q.where(AuditLog.tenant_id == tid)
    q = q.order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 1000))
    filas = db.scalars(q).all()
    return [
        {
            "id": r.id,
            "tenant_id": r.tenant_id,
            "actor_email": r.actor_email,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "detail": r.detail,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in filas
    ]


@app.get("/api/tenants")
def list_tenants(db: Session = Depends(get_db), user=Depends(require_user)):
    q = select(Tenant).order_by(Tenant.code)
    if not _is_admin(user):
        allowed = _explicit_tenant_ids(db, user)
        if allowed:
            q = q.where(Tenant.id.in_(allowed))
    tenants = db.scalars(q).all()
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
    _check_tenant_access(db, tenant, user)

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
    _check_tenant_access(db, tenant, user)

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
    _check_tenant_access(db, tenant, user)

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
        .order_by(Finding.id)
        .limit(1000)
    ).all()

    # Enriquecer los hallazgos de producto con el nombre y el stock, para que el
    # panel muestre QUÉ producto es (no solo su SKU) y priorice los que tienen
    # stock. Para subjects no-producto (atributo, categoría, ...) quedan en None.
    skus = [f.subject_key for f in filas if f.subject_type == "producto"]
    nombres: dict[str, str] = {}
    stock: dict[str, bool | None] = {}
    if skus:
        for sku, attrs in db.execute(
            select(ProductRecord.sku, ProductRecord.attributes).where(
                ProductRecord.tenant_id == tenant.id,
                ProductRecord.store_view_magento_id == store,
                ProductRecord.sku.in_(skus),
            )
        ).all():
            nombre = (attrs or {}).get("name")
            if nombre:
                nombres[sku] = nombre.strip()
        for sku, is_in_stock in db.execute(
            select(ProductSignal.sku, ProductSignal.is_in_stock).where(
                ProductSignal.tenant_id == tenant.id,
                ProductSignal.store_view_magento_id == store,
                ProductSignal.sku.in_(skus),
            )
        ).all():
            stock[sku] = is_in_stock

    def _prioridad(f: Finding) -> int:
        if f.subject_type != "producto":
            return 3  # no-producto, al final
        s = stock.get(f.subject_key)
        if s is True:
            return 0  # con stock primero
        if s is None:
            return 1  # sin datos de stock
        return 2  # sin stock

    return {
        "items": [
            {
                "subject_key": f.subject_key,
                "subject_type": f.subject_type,
                "name": nombres.get(f.subject_key) if f.subject_type == "producto" else None,
                "is_in_stock": stock.get(f.subject_key) if f.subject_type == "producto" else None,
                "evidence": f.evidence,
                "severity": f.severity,
            }
            for f in sorted(filas, key=_prioridad)
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
    _check_tenant_access(db, tenant, user)

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


def _regla_del_tenant(db: Session, tenant_code: str, rule_id: int, user: dict) -> Rule:
    """Resuelve una regla comprobando que pertenece al tenant. 404 si no."""
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    _check_tenant_access(db, tenant, user)
    _require_tenant_role(db, tenant, user, *CURATION_ROLES)
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
    user=Depends(require_user),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id, user)
    try:
        curation.aceptar(
            db, [regla.id], actor=user["email"],
            confirmar_ambiguo=body.confirmar_ambiguo,
        )
        record(db, action="rule.accept", actor_email=user["email"], tenant_id=regla.tenant_id, entity_type="rule", entity_id=regla.id)
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
    user=Depends(require_user),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id, user)
    try:
        curation.rechazar(db, regla.id, actor=user["email"], motivo=body.motivo)
        record(db, action="rule.reject", actor_email=user["email"], tenant_id=regla.tenant_id, entity_type="rule", entity_id=regla.id, detail={"motivo": body.motivo})
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
    user=Depends(require_user),
):
    regla = _regla_del_tenant(db, tenant_code, rule_id, user)
    try:
        curation.acotar(db, regla.id, actor=user["email"], motivo=body.motivo)
        record(db, action="rule.limit", actor_email=user["email"], tenant_id=regla.tenant_id, entity_type="rule", entity_id=regla.id, detail={"motivo": body.motivo})
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
    user=Depends(require_user),
):
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    _check_tenant_access(db, tenant, user)
    _require_tenant_role(db, tenant, user, *CURATION_ROLES)
    snap = curation.snapshot(db, tenant_id=tenant.id, store_view=store)
    record(db, action="rule.snapshot", actor_email=user["email"], tenant_id=tenant.id, entity_type="snapshot", entity_id=snap.id, detail={"store": store, "version": snap.version})
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
    _check_tenant_access(db, tenant, user)
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
    _check_tenant_access(db, tenant, user)

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
    _check_tenant_access(db, tenant, user)

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
    user=Depends(require_user),
):
    """Crea una regla `curada` en borrador desde el panel. Entra al mismo
    circuito de curación que las inferidas."""
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    _check_tenant_access(db, tenant, user)
    _require_tenant_role(db, tenant, user, *CURATION_ROLES)
    try:
        regla = curation.crear(
            db, tenant_id=tenant.id, store_view_magento_id=store,
            scope_kind=body.scope_kind, scope_key=body.scope_key, kind=body.kind,
            attribute=body.attribute, actor=user["email"],
            minimo=body.minimo, maximo=body.maximo,
        )
        record(db, action="rule.create", actor_email=user["email"], tenant_id=regla.tenant_id, entity_type="rule", entity_id=regla.id)
    except curation.ReglaDuplicada as e:
        raise HTTPException(409, detail=str(e))
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    db.commit()
    db.refresh(regla)
    return _regla_json(regla)


@app.get("/api/tenants/{tenant_code}/rules/{rule_id}/evidence")
def rule_evidence(
    tenant_code: str,
    rule_id: int,
    db: Session = Depends(get_db),
    user=Depends(require_user),
):
    """La evidencia de una regla, con los conflictos entre fuentes declarados.

    S2: toda propuesta muestra de dónde salió cada valor (fuente, fragmento,
    fecha, identidad, transformación) y un desacuerdo entre fuentes aparece
    como conflicto, sin resolverse en silencio.
    """
    tenant = db.scalars(select(Tenant).where(Tenant.code == tenant_code)).first()
    if not tenant:
        raise HTTPException(404, "Tenant no encontrado")
    _check_tenant_access(db, tenant, user)
    regla = db.get(Rule, rule_id)
    if regla is None or regla.tenant_id != tenant.id:
        raise HTTPException(404, "Regla no encontrada")

    def _json(e):
        return {
            "id": e.id,
            "datum": e.datum,
            "source": e.source,
            "fragment": e.fragment,
            "product_sku": e.product_sku,
            "observed_at": e.observed_at.isoformat() if e.observed_at else None,
            "transformation": e.transformation,
            "value": e.value,
            "conflict_state": e.conflict_state,
            "conflict_group": e.conflict_group,
        }

    return {
        "evidence": [_json(e) for e in list_for_subject(db, "rule", rule_id)],
        "conflicts": [
            [_json(e) for e in g] for g in conflicts_for_subject(db, "rule", rule_id)
        ],
    }


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
    _check_tenant_access(db, tenant, user)

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
