"""Explicit, fail-closed configuration for a single-origin public demonstration."""
from dataclasses import dataclass
from ipaddress import ip_address
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from starlette.responses import JSONResponse

from app.database import ROOT


def value(name, default=""):
    key = "SCOPESHIELD_" + name
    return os.environ.get(key, dotenv_values(ROOT / ".env").get(key, default)) or ""


def public_origin(raw):
    """Canonical HTTPS DNS origin only; no credentials, paths, IDN or wildcards."""
    try:
        url = urlsplit(raw)
        host = url.hostname or ""
        labels = host.split(".")
        if (url.scheme != "https" or url.netloc != host or url.path not in {"", "/"}
                or url.query or url.fragment or url.username or url.password
                or len(host) > 253 or len(labels) < 2 or host.endswith((".localhost", ".local"))
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
                or not re.search(r"[a-z]", labels[-1])):
            raise ValueError
        try:
            ip_address(host)
        except ValueError:
            return "https://" + host
    except (ValueError, TypeError):
        pass
    raise ValueError("Public URLs must be exact HTTPS DNS origins without ports, credentials or paths")


@dataclass(frozen=True)
class Deployment:
    production: bool = False
    origin: str = ""
    proxies: tuple[str, ...] = ()

    @property
    def origins(self):
        return (self.origin,) if self.production else ("http://127.0.0.1:3000", "http://localhost:3000")

    @property
    def hosts(self):
        return [urlsplit(self.origin).hostname] if self.production else ["127.0.0.1", "localhost"]

    @classmethod
    def load(cls):
        mode = value("ENV", "local")
        if mode not in {"local", "production"}:
            raise ValueError("SCOPESHIELD_ENV must be local or production")
        if mode == "local":
            return cls()
        origin = public_origin(value("FRONTEND_URL"))
        if public_origin(value("BACKEND_URL")) != origin:
            raise ValueError("The supported public profile requires one frontend/backend HTTPS origin")
        if value("SECURE_COOKIES") != "true" or value("DEMO_MODE") != "true":
            raise ValueError("Public demonstrations require Secure cookies and DEMO_MODE=true")
        if value("AI_PROVIDER") != "disabled" or value("AI_ALLOW_LIVE", "false") != "false":
            raise ValueError("Public demonstrations require disabled live AI")
        proxies = tuple(value("TRUSTED_PROXIES").split(","))
        if not proxies or any(not item or str(ip_address(item)) != item for item in proxies):
            raise ValueError("Configure exact trusted proxy IP addresses; networks and wildcards are unsupported")
        return cls(True, origin, proxies)

    def validate_storage(self, url):
        if not self.production:
            return
        directory = Path(value("DATA_DIR"))
        storage_id = value("STORAGE_ID")
        parsed = make_url(url)
        if (not directory.is_absolute() or directory.parent == directory or not directory.is_dir()
                or not os.path.ismount(directory) or not re.fullmatch(r"[a-zA-Z0-9_-]{16,80}", storage_id)
                or not parsed.database or parsed.get_backend_name() != "sqlite" or parsed.query):
            raise ValueError("Production requires an existing dedicated persistent mount and explicit SQLite URL")
        database = Path(parsed.database)
        if (not database.is_absolute() or database.resolve().parent != directory.resolve()
                or directory.resolve().is_relative_to(ROOT.resolve())):
            raise ValueError("Production SQLite must be directly inside the persistent mount, outside the checkout")
        marker = directory / ".scopeshield-volume"
        if not marker.is_file() or marker.read_text(encoding="ascii").strip() != storage_id:
            raise ValueError("Persistent volume identity does not match; refusing to initialize storage")
        # A restored snapshot must be acknowledged through the offline recovery CLI.
        if (directory / ".restore-pending").exists():
            raise ValueError("Restored storage is quarantined; complete offline recovery before startup")


class DeploymentBoundary:
    """Inspect the socket peer BEFORE trusting any supplied forwarding headers.

    Uvicorn MUST run with --no-proxy-headers. Only our explicit proxy applies them.
    Caddy overwrites X-Forwarded-For with one direct peer IP, never a client chain.
    """
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope.get("headers", [])
        forwarded = [(k.lower(), v) for k, v in headers if k.lower() in {
            b"forwarded", b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host", b"x-real-ip"}]
        def reject(status, message):
            return JSONResponse(status_code=status, content={"detail": message})(scope, receive, send)
        if not self.config.production:
            if forwarded:
                return await reject(400, {"code": "PROXY_HEADERS_UNSUPPORTED", "message": "Forwarded headers are unsupported locally"})
        else:
            hosts = [v.decode("latin1") for k, v in headers if k.lower() == b"host"]
            if hosts != [urlsplit(self.config.origin).netloc]:
                return await reject(400, "Invalid host")
            if forwarded:
                peer = (scope.get("client") or ("",))[0]
                names = [k for k, _ in forwarded]
                if (peer not in self.config.proxies or len(names) != len(set(names))
                        or set(names) != {b"x-forwarded-for", b"x-forwarded-proto"}):
                    return await reject(400, "Invalid proxy headers")
                assertions = dict(forwarded)
                try:
                    client_ip = str(ip_address(assertions[b"x-forwarded-for"].decode("ascii")))
                    if assertions[b"x-forwarded-proto"] != b"https":
                        raise ValueError
                except (ValueError, UnicodeError):
                    return await reject(400, "Invalid proxy headers")
                scope = dict(scope, scheme="https", client=(client_ip, 0),
                             headers=[(k, v) for k, v in headers if not k.lower().startswith(b"x-forwarded-")])
            if scope.get("scheme") != "https":
                return await reject(400, "HTTPS is required")
        return await self.app(scope, receive, send)
