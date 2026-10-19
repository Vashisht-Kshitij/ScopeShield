"""Offline HTTPS Chrome QA, bound only to loopback. NEVER a deployment entry point.

Requires a production same-origin frontend build, Chrome, and Git's local openssl.
Only persistent-mount detection is substituted on Windows temporary storage. Host,
proxy, HTTPS, cookies, CSRF, permissions, budgets and process locks remain real.
"""
from contextlib import redirect_stdout
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from uuid import uuid4
from scripts.tooling import pnpm_executable

ROOT = Path(__file__).resolve().parents[1]


def qa_app():
    if os.environ.get("SCOPESHIELD_LOCAL_TLS_QA") != "isolated-loopback-only":
        raise RuntimeError("This test-only factory requires the isolated QA runner")
    import requests
    from app.database import database_url
    from app.deployment import Deployment
    from app.main import create_app
    def forbidden(*args, **kwargs):
        raise AssertionError("No external HTTP in deployment QA")
    requests.sessions.Session.request = forbidden
    original = Deployment.validate_storage
    def local_volume(self, url):
        # All real storage checks still run, except that this disposable directory
        # is recognized as a mount for this QA subprocess only.
        from unittest.mock import patch
        directory = Path(os.environ["SCOPESHIELD_DATA_DIR"]).resolve()
        if (url != database_url() or directory.parent != Path(tempfile.gettempdir()).resolve()
                or not directory.name.startswith("scopeshield-day14-")):
            raise RuntimeError("Invalid isolated storage")
        with patch("app.deployment.os.path.ismount", return_value=True):
            original(self, url)
    Deployment.validate_storage = local_volume
    return create_app()


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def do_PATCH(self): self.forward()
    def do_DELETE(self): self.forward()
    def do_OPTIONS(self): self.forward()

    def forward(self):
        if self.headers.get("Host") != "demo.example.test":
            self.send_error(400)
            return
        api = self.path.startswith("/api/") or self.path == "/health"
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {
            "host", "connection", "forwarded", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-real-ip"}}
        headers["Host"] = "demo.example.test" if api else "127.0.0.1:3000"
        if api:
            headers.update({"X-Forwarded-For": self.client_address[0], "X-Forwarded-Proto": "https"})
        upstream = http.client.HTTPConnection("127.0.0.1", 8002 if api else 3000, timeout=30)
        started = False
        try:
            upstream.request(self.command, self.path, body=data, headers=headers)
            result = upstream.getresponse()
            body = result.read()
            self.send_response(result.status)
            started = True
            for key, value in result.getheaders():
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (OSError, http.client.HTTPException):
            if not started:
                try:
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(b"Test upstream is restarting")
                except OSError:
                    pass  # A disconnected test browser requires no response.
        finally:
            upstream.close()


class LocalTLSServer(ThreadingHTTPServer):
    # Complete TLS handshakes in a worker. Chrome's idle preconnect socket must
    # not block the accept loop and prevent other script/API requests hydrating.
    def finish_request(self, request, client_address):
        request.settimeout(15)
        try:
            with self.tls_context.wrap_socket(request, server_side=True) as secured:
                self.RequestHandlerClass(secured, client_address, self)
        except (OSError, ssl.SSLError):
            request.close()


def pdf_fixture(output):
    """Two physical pages of the unchanged offline agreement, no provider calls."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    lines = (ROOT / "examples/day10_sandbox_agreement.txt").read_text(encoding="utf-8").splitlines()
    pivot = next(index for index, line in enumerate(lines) if line.startswith("Milestone 2"))
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding')})
    font_ref = writer._add_object(font)
    for section in (lines[:pivot], lines[pivot:]):
        page = writer.add_blank_page(width=1000, height=1400)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_ref})})
        commands = ['BT /F1 10 Tf 14 TL 40 1350 Td']
        for line in section:
            escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
            commands.append(f'({escaped}) Tj T*')
        stream = DecodedStreamObject(); stream.set_data(('\n'.join(commands) + '\nET').encode('cp1252'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    with (output / 'synthetic-agreement.pdf').open('xb') as handle: writer.write(handle)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for port in (3000, 8002, 8443):
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("A deployment QA port is already in use")
    output = ROOT / ".local/day15" / ("https-" + uuid4().hex)
    output.mkdir(parents=True)
    pdf_fixture(output)
    temporary = tempfile.TemporaryDirectory(prefix="scopeshield-day14-")
    volume = Path(temporary.name)
    identity = "synthetic-volume-identity"
    (volume / ".scopeshield-volume").write_text(identity)
    openssl = Path(os.environ.get("SCOPESHIELD_OPENSSL", "C:/Program Files/Git/usr/bin/openssl.exe" if os.name == "nt" else "/usr/bin/openssl"))
    subprocess.run([str(openssl), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(output / "key.pem"), "-out", str(output / "cert.pem"), "-subj", "/CN=demo.example.test",
                    "-addext", "subjectAltName=DNS:demo.example.test"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    password = secrets.token_urlsafe(24)
    env = {**os.environ, "SCOPESHIELD_ENV": "production", "SCOPESHIELD_FRONTEND_URL": "https://demo.example.test",
           "SCOPESHIELD_BACKEND_URL": "https://demo.example.test", "SCOPESHIELD_SECURE_COOKIES": "true", "SCOPESHIELD_DEMO_MODE": "true",
           "SCOPESHIELD_AI_PROVIDER": "disabled", "SCOPESHIELD_AI_ALLOW_LIVE": "false", "SCOPESHIELD_TRUSTED_PROXIES": "127.0.0.1",
           "SCOPESHIELD_DATA_DIR": str(volume), "SCOPESHIELD_STORAGE_ID": identity,
           "SCOPESHIELD_DATABASE_URL": "sqlite:///" + (volume / "qa.db").as_posix(),
           "SCOPESHIELD_LOCAL_TLS_QA": "isolated-loopback-only", "SCOPESHIELD_E2E_PASSWORD": password,
           "SCOPESHIELD_E2E_ARTIFACTS": str(output), "NEXT_TELEMETRY_DISABLED": "1"}
    from sqlalchemy.orm import Session
    from app.database import build_engine, initialize_database
    from scripts.provision_judge import provision
    engine = build_engine(env["SCOPESHIELD_DATABASE_URL"])
    with redirect_stdout(io.StringIO()):
        initialize_database(engine)
        with Session(engine) as db:
            provision(db, "judge.one", password)
            provision(db, "judge.two", password, prepared=True)
            provision(db, "judge.conversation", password)
    engine.dispose()
    pnpm = pnpm_executable()
    commands = [[sys.executable, "-B", "-m", "uvicorn", "scripts.run_deployment_tests:qa_app", "--factory", "--host", "127.0.0.1", "--port", "8002", "--no-proxy-headers", "--no-access-log"], [pnpm, "start"]]
    processes, logs = [], []
    stopped = threading.Event()
    server = LocalTLSServer(("127.0.0.1", 8443), Proxy)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(output / "cert.pem", output / "key.pem")
    server.tls_context = context
    def start(index):
        process = subprocess.Popen(commands[index], cwd=ROOT if index == 0 else ROOT / "frontend", env=env,
                                   stdout=logs[index], stderr=logs[index], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return process
    def terminate(process):
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else: process.terminate()
            process.wait(timeout=15)
    def ready():
        for _ in range(160):
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("A QA service failed startup; inspect isolated logs")
            try:
                with urllib.request.urlopen("http://127.0.0.1:3000", timeout=1):
                    request = urllib.request.Request("http://127.0.0.1:8002/health", headers={"Host": "demo.example.test", "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"})
                    with urllib.request.urlopen(request, timeout=1): return
            except OSError: time.sleep(.25)
        raise RuntimeError("Frontend did not start")
    def restart():
        while not stopped.wait(.1):
            if (output / "restart-request.json").exists():
                before = processes[0].pid
                terminate(processes[0])
                processes[0] = start(0)
                (output / "restart-complete.json").write_text(json.dumps({"before": before, "after": processes[0].pid}))
                return
    watcher = threading.Thread(target=restart, daemon=True)
    try:
        for index in range(2):
            logs.append((output / f"service-{index}.log").open("w", encoding="utf-8"))
            processes.append(start(index))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        ready()
        watcher.start()
        result = subprocess.Popen([pnpm, "exec", "playwright", "test", "--config", "playwright.production.config.ts"], cwd=ROOT / "frontend", env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for line in result.stdout:
            print(line.replace(password, "[redacted]"), end="", flush=True)
        code = result.wait()
        print(json.dumps({"https_artifacts": str(output), "exit_code": code, "mount_detection_simulated": True}))
        return code
    finally:
        stopped.set()
        if watcher.is_alive(): watcher.join(20)
        server.shutdown()
        server.server_close()
        for process in reversed(processes): terminate(process)
        for log in logs: log.close()
        database = volume / "qa.db"
        if database.is_file():
            from scripts.sqlite_backup import backup
            backup(database, output / "qa.db")
        # Only this exact TemporaryDirectory, never a caller-supplied path.
        if volume.parent != Path(tempfile.gettempdir()).resolve() or not volume.name.startswith("scopeshield-day14-"):
            raise RuntimeError("Invalid temporary cleanup path")
        temporary.cleanup()
        (output / "key.pem").unlink(missing_ok=True)
        for path in output.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".log", ".md"}:
                value = path.read_text(encoding="utf-8", errors="replace")
                if password in value:
                    path.write_text(value.replace(password, "[redacted]"), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
