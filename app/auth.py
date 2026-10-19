"""Local authentication and deny-by-default resource authorization.

No registration, role mutation, password recovery or production bypass endpoint exists.
Password hashes use scrypt; random session verifiers are stored only as SHA-256 hashes.
"""
from datetime import timedelta
import hashlib
import hmac
import os
import re
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, SecretStr
from dotenv import dotenv_values
from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.contract_schemas import StrictModel
from app.database import ROOT, get_db
from app.resources import rate, setting
from app.storage import (AuthSession, ChangeOrder, ChangeRequest, Contract, InvoiceLink,
                         LoginLimit, ManualInvoiceOwner, Milestone, Project, SecurityEvent, User, utc_now)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
COOKIE = "scopeshield_session"
LOGIN_COOKIE = "scopeshield_login"
SESSION_SECONDS = 8 * 60 * 60
MUTATIONS = {"POST", "PATCH", "PUT", "DELETE"}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(token: str, purpose="session") -> str:
    return hmac.new(token.encode(), purpose.encode(), hashlib.sha256).hexdigest()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password of 12–128 characters")
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024)
    return "scrypt$131072$8$1$" + salt.hex() + "$" + value.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if (kind, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), maxmem=256 * 1024 * 1024)
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError):
        return False


# Equal-cost verification for unknown accounts; no usable default account/password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9@._+-]{2,99}", value):
        raise ValueError("Use 3–100 letters, numbers or @ . _ + - for the username")
    return value


def cookie_options(request: Request) -> dict:
    if request.app.state.deployment.production:
        return dict(httponly=True, samesite="strict", secure=True, path="/")
    # Plain HTTP is supported exclusively on loopback. Secure can be required for a local TLS proxy.
    configured = os.getenv("SCOPESHIELD_SECURE_COOKIES")
    if configured is None:
        configured = dotenv_values(ROOT / ".env").get("SCOPESHIELD_SECURE_COOKIES", "false")
    setting = str(configured).strip().lower()
    if setting not in {"true", "false"}:
        raise HTTPException(503, "SCOPESHIELD_SECURE_COOKIES must be true or false")
    return dict(httponly=True, samesite="strict", secure=request.url.scheme == "https" or setting == "true", path="/")


def require_csrf(request: Request, token: str, purpose="session"):
    submitted = request.headers.get("x-csrf-token", "")
    if not token or len(submitted) != 64 or not submitted.isascii() or not hmac.compare_digest(submitted, csrf_token(token, purpose)):
        raise HTTPException(403, "Missing or invalid CSRF token; refresh your session")


def current_user(request: Request, session: Session) -> User:
    token = request.cookies.get(COOKIE, "")
    row = session.get(AuthSession, digest(token)) if token else None
    user = session.get(User, row.user_id) if row and row.expires_at > utc_now() else None
    if user is None or not user.active:
        raise HTTPException(401, "Sign in to continue")
    if request.method in MUTATIONS:
        require_csrf(request, token)
    session.info["actor"] = user
    return user


def actor(session: Session) -> User:
    user = session.info.get("actor")
    if user is None:
        raise HTTPException(401, "Sign in to continue")
    return user


def own_project(session: Session, project_id: str):
    row = session.get(Project, project_id)
    if row is None or row.owner_id != actor(session).id:
        raise HTTPException(404, "Resource not found")
    return row


def invoice_owned(session: Session, invoice_id: str):
    receipt = session.get(ManualInvoiceOwner, invoice_id)
    if receipt and receipt.owner_id == actor(session).id:
        return
    link = session.scalar(select(InvoiceLink).where(InvoiceLink.invoice_id == invoice_id))
    if link:
        contract = session.get(Contract, link.contract_id)
        own_project(session, contract.project_id)
        return
    raise HTTPException(404, "Resource not found")


def authorize_workspace(request: Request, session: Session = Depends(get_db)):
    """Application dependency runs before provider dependencies on EVERY API route.

    Client access uses a separate minimal DTO router; ordinary workspace routes are
    freelancer-only. Every identifier here is resolved through its project's owner.
    Catalog filtering and multipart parent checks are also enforced at their queries.
    """
    path = request.url.path
    if not path.startswith("/api/") or path.startswith("/api/auth/"):
        return
    user = current_user(request, session)
    # Release the read connection before independent durable admission transactions.
    # Otherwise concurrent requests can each hold one pool connection while waiting
    # for a second, exhausting the pool before any can finish admission.
    session.reset()  # Keeps session.info/loaded actor; no domain writes exist here.
    request.state.user_id = user.id
    category = "READ" if request.method == "GET" else "WRITE"
    rate(session.get_bind(), category, user.id, setting("RATE_" + category, 600 if category == "READ" else 120))
    if request.method == "GET" and (path in {"/api/contracts", "/api/projects", "/api/client/projects"} or path.endswith(("/activity", "/history", "/approval-history", "/change-requests", "/sharing", "/client-responses")) or path.startswith("/api/client/projects/")):
        rate(session.get_bind(), "QUERY", user.id, setting("RATE_QUERY", 60))
    if request.method == "POST" and path == "/api/contracts":
        category = "UPLOAD"
    elif request.method == "GET" and path.endswith("/original"):
        category = "DOCUMENT_DOWNLOAD"
    elif path.endswith("/extract"):
        category = "EXTRACT"
    elif path.endswith("/analyze"):
        category = "ANALYZE"
    elif ("/invoice/" in path and path.endswith(("/sync", "/reconcile", "/send"))) or (path.startswith("/api/invoices/") and request.method == "GET") or (path.endswith("/invoice") and request.method == "POST"):
        category = "PROVIDER"
    else:
        category = None
    if category:
        rate(session.get_bind(), category, user.id, setting("RATE_" + category, 10 if category == "DOCUMENT_DOWNLOAD" else 20))
    if path.startswith("/api/client/"):
        if user.role != "client":
            raise HTTPException(403, "A client account is required")
        return
    if user.role != "freelancer":
        raise HTTPException(403, "A freelancer account is required")
    if request.app.state.deployment.production and (
        ("/invoice" in path and request.method != "GET")
        or path.endswith("/invoice/preview")
        or (path.startswith("/api/invoices/") and not path.endswith("/history"))
    ):
        raise HTTPException(403, detail={"code": "DEMO_PROVIDER_DISABLED", "message": "Public demo: provider invoice actions are disabled. Review and saved records remain available."})
    for key, value in request.path_params.items():
        value = str(value)
        if key == "invoice_id":
            # Leave malformed identifiers to the endpoint's strict validator.
            if re.fullmatch(r"INV2-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value) and 10 <= len(value) <= 85:
                invoice_owned(session, value)
            continue
        if key not in {"project_id", "contract_id", "milestone_id", "request_id", "order_id"}:
            raise HTTPException(403, "Resource policy is not configured")
        try:
            value = str(UUID(value))
        except ValueError:
            continue
        if key == "project_id":
            own_project(session, value)
            continue
        model = {"contract_id": Contract, "milestone_id": Milestone, "request_id": ChangeRequest, "order_id": ChangeOrder}.get(key)
        if model is None:
            raise HTTPException(403, "Resource policy is not configured")
        row = session.get(model, value)
        if row is None:
            raise HTTPException(404, "Resource not found")
        if model is ChangeOrder:
            row = session.get(ChangeRequest, row.change_request_id)
        if isinstance(row, Milestone):
            row = session.get(Contract, row.contract_id)
        own_project(session, row.project_id)


class Login(StrictModel):
    username: str = Field(min_length=3, max_length=100)
    password: SecretStr = Field(min_length=1, max_length=128)


def user_read(user: User) -> dict:
    return dict(id=user.id, username=user.username, display_name=user.display_name, role=user.role)


@router.get("/csrf")
def login_csrf(request: Request, response: Response):
    token = secrets.token_urlsafe(32)
    response.set_cookie(LOGIN_COOKIE, token, max_age=600, **cookie_options(request))
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": csrf_token(token, "login")}


def count_login(session: Session, key: str, limit: int):
    """SQLite upsert reserves attempts atomically before the expensive password KDF."""
    now = utc_now()
    statement = sqlite_insert(LoginLimit).values(key=key, attempts=1, reset_at=now + timedelta(minutes=15))
    statement = statement.on_conflict_do_update(index_elements=[LoginLimit.key], set_={"attempts": LoginLimit.attempts + 1},
        where=(LoginLimit.reset_at > now) & (LoginLimit.attempts < limit))
    # Expired rows are cleaned first, within the same write transaction.
    session.execute(delete(LoginLimit).where(LoginLimit.reset_at <= now))
    result = session.execute(statement)
    session.commit()
    if result.rowcount != 1:
        raise HTTPException(429, "Too many sign-in attempts; try again in 15 minutes", headers={"Retry-After": "900"})


@router.post("/login")
def login(payload: Login, request: Request, response: Response, session: Session = Depends(get_db)):
    require_csrf(request, request.cookies.get(LOGIN_COOKIE, ""), "login")
    username = payload.username.strip().lower()
    count_login(session, digest("ip:" + (request.client.host if request.client else "local")), setting("LOGIN_IP_ATTEMPTS", 40))
    count_login(session, digest("account:" + username), setting("LOGIN_ACCOUNT_ATTEMPTS", 10))
    user = session.scalar(select(User).where(User.username == username))
    with request.app.state.resources.slot("password"):
        valid = verify_password(payload.password.get_secret_value(), user.password_hash if user else _DUMMY_HASH)
    if not valid or user is None or not user.active:
        raise HTTPException(401, "Username or password is incorrect")
    # Rotate, including any existing authenticated session on this browser.
    session.execute(delete(AuthSession).where((AuthSession.expires_at <= utc_now()) | (AuthSession.token_hash == digest(request.cookies.get(COOKIE, "")))))
    token = secrets.token_urlsafe(32)
    session.add(AuthSession(token_hash=digest(token), user_id=user.id, expires_at=utc_now() + timedelta(seconds=SESSION_SECONDS)))
    session.add(SecurityEvent(actor_id=user.id, action="LOGIN"))
    session.commit()
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, **cookie_options(request))
    response.delete_cookie(LOGIN_COOKIE, **cookie_options(request))
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_read(user), "csrf_token": csrf_token(token), "demo_mode": request.app.state.deployment.production}


@router.get("/me")
def me(request: Request, response: Response, session: Session = Depends(get_db)):
    user = current_user(request, session)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user_read(user), "csrf_token": csrf_token(request.cookies[COOKIE]), "demo_mode": request.app.state.deployment.production}


@router.post("/logout")
def logout(request: Request, response: Response, session: Session = Depends(get_db)):
    user = current_user(request, session)
    session.execute(delete(AuthSession).where(AuthSession.token_hash == digest(request.cookies[COOKIE])))
    session.add(SecurityEvent(actor_id=user.id, action="LOGOUT"))
    session.commit()
    response.delete_cookie(COOKIE, **cookie_options(request))
    response.headers["Cache-Control"] = "no-store"
    return {"signed_out": True}
