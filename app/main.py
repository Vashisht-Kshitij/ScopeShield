"""Local-only ScopeShield API: create, inspect, and explicitly send sandbox drafts."""
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders

from app.models import InvoiceID, InvoiceResponse
from app.paypal import PayPalAPIError, PayPalConfigurationError, PayPalSandbox
from app.contracts import router as contracts_router
from app.database import build_engine, database_url, initialize_database
from app.ingestion import UploadBodyLimit
from sqlalchemy.exc import SQLAlchemyError
from app.auth import authorize_workspace, router as auth_router
from app.reviews import router as reviews_router
from app.extraction import ExtractionError, sanitized_extraction_error
from app.dependencies import get_paypal
from app.milestone_invoices import router as milestone_invoices_router
from app.scope_changes import router as scope_changes_router
from app.workspace import router as workspace_router
from app.resources import single_instance, error_headers
from app.deployment import Deployment, DeploymentBoundary

invoice_router = APIRouter()
FRONTEND_ORIGINS = ("http://127.0.0.1:3000", "http://localhost:3000")


class BrowserSafety:
    """Pure ASGI headers/origin policy, without a second body/response task bridge."""
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if request.url.path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
                if self.config.production:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)
        # CORS alone does not prevent a foreign page submitting a simple POST.
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin is not None:
            allowed = self.config.origins if self.config.production else (*self.config.origins, str(request.base_url).rstrip("/"))
            if origin not in allowed:
                return await JSONResponse(status_code=403, content={"detail": "Cross-origin mutations are not allowed"})(scope, receive, secured_send)
        await self.app(scope, receive, secured_send)


async def configuration_error(request: Request, exc: PayPalConfigurationError):
    return JSONResponse(status_code=503, content={"detail": "Local PayPal sandbox environment is not configured"})


async def paypal_error(request: Request, exc: PayPalAPIError):
    detail = {
        "provider": "paypal_sandbox", "operation": exc.operation,
        "upstream_status": exc.upstream_status, "error": exc.name, "debug_id": exc.debug_id,
    }
    if exc.invoice_id:
        detail.update(invoice_id=exc.invoice_id, completed_action=exc.completed_action)
    return JSONResponse(status_code=exc.http_status, content={"detail": detail})


async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo rejected user inputs, which can accidentally contain secrets.
    return JSONResponse(status_code=422, content={"detail": [
        {key: error[key] for key in ("type", "loc", "msg")}
        for error in exc.errors()
    ]})


@invoice_router.get("/health", summary="Check local API liveness")
def health():
    # Liveness does not authenticate with PayPal or assert that credentials work.
    return {"status": "ok", "environment": "paypal_sandbox"}


def disabled_legacy_invoice_mutation():
    # No provider dependency: even OAuth/configuration must be unreachable here.
    raise HTTPException(410, detail={
        "code": "LEGACY_INVOICE_MUTATION_DISABLED",
        "message": "Use the approved milestone invoice preview and confirmed milestone invoice actions. Legacy invoice creation and sending are disabled.",
    })


@invoice_router.post("/api/invoices", status_code=410, deprecated=True, summary="Disabled legacy draft creation",
                     responses={410: {"description": "Use the approved milestone invoice workflow"}})
def create_invoice():
    disabled_legacy_invoice_mutation()


@invoice_router.get("/api/invoices/{invoice_id}", response_model=InvoiceResponse, summary="Read the PayPal sandbox status")
def get_invoice(invoice_id: InvoiceID, paypal: PayPalSandbox = Depends(get_paypal)):
    return paypal.get_invoice(invoice_id)


@invoice_router.post("/api/invoices/{invoice_id}/send", status_code=410, deprecated=True, summary="Disabled legacy invoice sending",
                     responses={410: {"description": "Use the confirmed milestone invoice send action"}})
def send_invoice(invoice_id: InvoiceID):
    disabled_legacy_invoice_mutation()


async def database_error(request: Request, exc: SQLAlchemyError):
    import sqlite3
    if getattr(getattr(exc, "orig", None), "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
        return JSONResponse(status_code=507, content={"detail": {"code": "DATABASE_STORAGE_FULL",
            "message": "The recoverable database storage limit was reached. Saved records are preserved; ask the operator to review capacity."}})
    return JSONResponse(status_code=503, content={"detail": "Local contract storage is unavailable"})


async def extraction_error(request: Request, exc: ExtractionError):
    error = sanitized_extraction_error(exc)
    return JSONResponse(status_code=error.status_code, headers=error_headers(error), content={"detail": {"code": error.code, "message": error.message}})


def create_app(*, storage_url: str | None = None) -> FastAPI:
    config = Deployment.load()
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        url = storage_url or database_url()
        config.validate_storage(url)
        engine = build_engine(url)
        try:
            with single_instance(engine) as resources:
                initialize_database(engine)
                application.state.engine = engine
                application.state.resources = resources
                yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="ScopeShield API", version="0.9.0", lifespan=lifespan,
        docs_url=None if config.production else "/docs", redoc_url=None if config.production else "/redoc",
        openapi_url=None if config.production else "/openapi.json", redirect_slashes=not config.production,
        dependencies=[Depends(authorize_workspace)],
        description=("Local contract intelligence and PayPal Sandbox invoicing. "
                     "Extraction saves facts for review and never calls PayPal. "
                     "Scope comparisons and change orders require explicit human review. "
                     "offline_demo is a synthetic fixture, not live AI."),
    )
    application.state.deployment = config
    application.add_middleware(UploadBodyLimit)
    application.add_middleware(BrowserSafety, config=config)
    application.add_middleware(CORSMiddleware, allow_origins=list(config.origins),
                               allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                               allow_headers=["Content-Type", "X-CSRF-Token"], expose_headers=["Retry-After"])
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=config.hosts, www_redirect=False)
    application.add_middleware(DeploymentBoundary, config=config)
    application.add_exception_handler(PayPalConfigurationError, configuration_error)
    application.add_exception_handler(PayPalAPIError, paypal_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(SQLAlchemyError, database_error)
    application.add_exception_handler(ExtractionError, extraction_error)
    application.include_router(invoice_router)
    application.include_router(contracts_router)
    application.include_router(reviews_router)
    application.include_router(milestone_invoices_router)
    application.include_router(scope_changes_router)
    application.include_router(workspace_router)
    from app.evidence_pack import router as evidence_pack_router
    application.include_router(evidence_pack_router)
    from app.demo import router as demo_router
    application.include_router(demo_router)
    application.include_router(auth_router)
    from app.sharing import router as sharing_router
    application.include_router(sharing_router)
    return application


app = create_app()
