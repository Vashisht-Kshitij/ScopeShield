"""Bounded, in-memory PDF/UTF-8 ingestion; original files are never executed."""
import hashlib
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import HTTPException
from starlette.responses import JSONResponse

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
MAX_TEXT_CHARACTERS = 100_000
MAX_PDF_PAGES = 50


def upload_error(code: str, message: str, status: int = 422):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class UploadBodyLimit:
    """Bound every API body before parsing, including chunked anonymous requests."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        # Uvicorn can rewrite scope.client before calling the app when proxy headers
        # are enabled. This loopback profile admits no proxy assertions at all.
        if not scope["app"].state.deployment.production and any(key.lower() in {b"forwarded", b"x-forwarded-for", b"x-forwarded-proto", b"x-real-ip"} for key, _ in scope.get("headers", [])):
            return await JSONResponse(status_code=400, content={"detail": {"code": "PROXY_HEADERS_UNSUPPORTED", "message": "Forwarded client headers are unsupported in this local deployment profile"}})(scope, receive, send)
        from app.resources import rate, setting
        application = scope["app"]
        try:
            await asyncio.to_thread(rate, application.state.engine, "INGRESS", (scope.get("client") or ("local",))[0], setting("RATE_INGRESS", 600))
        except HTTPException as exc:
            return await JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)(scope, receive, send)
        if scope.get("method") not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await self.app(scope, receive, send)
        upload = scope.get("path", "").rstrip("/") == "/api/contracts" and scope.get("method") == "POST"
        maximum = MAX_REQUEST_BYTES if upload else setting("JSON_BODY_BYTES", 1024 * 1024, maximum=1024 * 1024)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = -1
        if declared < 0 or declared > maximum:
            response = JSONResponse(status_code=413, content={"detail": {"code": "UPLOAD_TOO_LARGE" if upload else "REQUEST_TOO_LARGE", "message": "Request exceeds the permitted body size"}})
            return await response(scope, receive, send)
        try:
            with application.state.resources.slot("body"):
                buffered, received = bytearray(), 0
                async with asyncio.timeout(setting("BODY_TIMEOUT_SECONDS", 15, maximum=30)):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        received += len(message.get("body", b""))
                        if received > maximum:
                            raise upload_error("UPLOAD_TOO_LARGE" if upload else "REQUEST_TOO_LARGE", "Request exceeds the permitted body size", 413)
                        buffered.extend(message.get("body", b""))
                        if not message.get("more_body", False):
                            break
                body = bytes(buffered)
                delivered = False
                async def bounded_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()
                await self.app(scope, bounded_receive, send)
        except TimeoutError:
            await JSONResponse(status_code=408, content={"detail": {"code": "REQUEST_TIMEOUT", "message": "Upload timed out; try a smaller document"}})(scope, receive, send)
        except HTTPException as exc:
            await JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)(scope, receive, send)


@dataclass(frozen=True)
class IngestedContract:
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    text: str
    page_count: int | None
    pdf_provenance: dict | None = None


def ingest(filename: str | None, media_type: str | None, data: bytes) -> IngestedContract:
    safe_name = (filename or "").replace("\\", "/").split("/")[-1]
    if not safe_name or len(safe_name) > 180 or any(ord(char) < 32 for char in safe_name):
        raise upload_error("INVALID_FILENAME", "Provide a valid filename up to 180 characters", 400)
    extension = PurePosixPath(safe_name).suffix.lower()
    expected = {".txt": "text/plain", ".pdf": "application/pdf"}.get(extension)
    if not expected or (media_type or "").split(";", 1)[0].lower() not in {expected, "application/octet-stream"}:
        raise upload_error("UNSUPPORTED_FILE_TYPE", "Upload a PDF or UTF-8 .txt contract with the matching media type", 415)
    if not data:
        raise upload_error("EMPTY_UPLOAD", "The uploaded contract is empty", 400)
    if len(data) > MAX_UPLOAD_BYTES:
        raise upload_error("UPLOAD_TOO_LARGE", "Contract files must be at most 5 MiB", 413)
    page_count, provenance = None, None
    if extension == ".txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise upload_error("INVALID_TEXT_ENCODING", "Plain-text contracts must use UTF-8", 400) from None
        if text.lstrip().startswith("%PDF-") or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise upload_error("INVALID_TEXT_FILE", "The .txt upload contains binary content", 400)
    else:
        if not data.startswith(b"%PDF-"):
            raise upload_error("INVALID_PDF", "The file does not contain a valid PDF", 400)
        text, page_count, provenance = parse_pdf(data, include_provenance=True)
        if not text.strip():
            raise upload_error("PDF_HAS_NO_TEXT", "This PDF has no extractable text. Scanned contracts require OCR, which is not supported yet")
    text = text.strip()
    if not text:
        raise upload_error("EMPTY_CONTRACT_TEXT", "The contract contains no text", 400)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise upload_error("CONTRACT_TEXT_TOO_LONG", "Contract text must be at most 100,000 characters", 413)
    return IngestedContract(safe_name, expected, len(data), hashlib.sha256(data).hexdigest(), text, page_count, provenance)


def parse_pdf(data, *, include_provenance=False):
    """No temporary files or inherited credentials. Timeout always kills and reaps."""
    import os
    from app.resources import setting
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
    worker = subprocess.Popen([sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        raw, _ = worker.communicate(data, timeout=setting("PDF_TIMEOUT_SECONDS", 20, maximum=30))
    except BaseException as exc:
        worker.kill()
        worker.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise upload_error("PDF_PROCESSING_TIMEOUT", "PDF processing timed out. Upload a simpler PDF or UTF-8 text", 422) from None
        raise
    try:
        # 100k non-BMP characters can require 1.2 MB when JSON uses surrogate escapes.
        if worker.returncode or len(raw) > 2 * 1024 * 1024:
            raise ValueError
        result = json.loads(raw)
        if "error" in result:
            errors = {"ENCRYPTED_PDF": (422, "Password-protected PDFs are not supported"),
                      "PDF_TOO_MANY_PAGES": (413, "PDF contracts must contain at most 50 pages"),
                      "PDF_EXPANDED_LIMIT": (413, "PDF expanded content exceeds the 8 MiB processing limit"),
                      "CONTRACT_TEXT_TOO_LONG": (413, "Extracted contract text must be at most 100,000 characters")}
            status, message = errors[result["error"]]
            raise upload_error(result["error"], message, status)
        from app.pdf_provenance import verified_provenance
        text, count = result["text"], result["page_count"]
        if not isinstance(text, str) or type(count) is not int or not 1 <= count <= MAX_PDF_PAGES:
            raise ValueError
        provenance = verified_provenance(text.strip(), count, result.get("pdf_provenance"))
        if provenance is not None:
            provenance = {**provenance, "document_sha256": hashlib.sha256(data).hexdigest()}
        return (text, count, provenance) if include_provenance else (text, count)
    except (ValueError, KeyError, TypeError):
        raise upload_error("INVALID_PDF", "The PDF could not be parsed within the safe resource limits", 400) from None
