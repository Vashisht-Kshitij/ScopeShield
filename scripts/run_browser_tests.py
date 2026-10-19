"""Run production frontend + isolated synthetic backend + real Chrome Playwright.

Creates random, ephemeral QA account passwords in memory, never logs or writes them.
No trace/video captures login secrets. Servers are stopped even on test failure.
Run after `pnpm build`: python -m scripts.run_browser_tests
"""
import os
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import threading
from unittest.mock import patch
from uuid import uuid4
import urllib.request

from scripts import manage_users
from scripts.tooling import pnpm_executable

ROOT = Path(__file__).resolve().parents[1]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day20", action="store_true", help="Include local-only AG Studio evaluation with the synthetic project fixture")
    parser.add_argument("--day24", action="store_true", help="Include the public-core Cedar release rehearsal; external providers remain blocked")
    parser.add_argument("--day24-only", action="store_true", help="Run only the Cedar workflow; requires --day24")
    parser.add_argument("--studio-only", action="store_true", help="Run only Studio browser QA; requires --day20")
    parser.add_argument("--day19", action="store_true", help="Prepare the isolated synthetic project intelligence walkthrough")
    parser.add_argument("--day18", action="store_true", help="Keep Evidence Pack browser QA in its own synthetic artifact directory")
    parser.add_argument("--day17", action="store_true", help="Keep the complete conversation browser QA artifacts in their own isolated directory")
    parser.add_argument("--day10", action="store_true", help="Include the complete walkthrough and real backend restart")
    live_mode=parser.add_mutually_exclusive_group()
    live_mode.add_argument("--live-day10",action="store_true",help="Prepare a guarded UI run; every real service operation still waits for separate approval")
    live_mode.add_argument("--live-paypal-day10",action="store_true",help="Use explicit offline AI and prepare a separately approved real PayPal draft; never call Gemini")
    args = parser.parse_args()
    if args.studio_only and not args.day20: parser.error("--studio-only requires --day20")
    if args.day24_only and not args.day24: parser.error("--day24-only requires --day24")
    if args.day20: args.day19 = True
    real_services=args.live_day10 or args.live_paypal_day10
    if args.day24 and (real_services or args.day19 or args.day20):
        parser.error("Day 24 is an offline core-only rehearsal; do not combine live or Studio fixtures")
    if real_services:args.day10=True
    for port in (3000, 8001):
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is already in use; stop its local server before isolated browser QA")
    runtime = Path(pnpm_executable())
    password = secrets.token_urlsafe(24)
    output = ROOT / ".local/day19" / ("run-"+uuid4().hex) if args.day19 else ROOT / ".local/day18" / ("run-"+uuid4().hex) if args.day18 else ROOT / ".local/day17" / ("run-"+uuid4().hex) if args.day17 else ROOT / ".local/day10" / ("run-"+uuid4().hex) if args.day10 else ROOT / ".local/day9"
    if args.day20: output = ROOT / ".local/day20" / ("run-"+uuid4().hex)
    if args.day24: output = ROOT / ".local/day24" / ("run-"+uuid4().hex)
    db_path = output / ("browser-" + uuid4().hex + ".db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "SCOPESHIELD_DATABASE_URL": "sqlite:///" + db_path.as_posix(),
           "SCOPESHIELD_AI_PROVIDER": "disabled", "SCOPESHIELD_AI_ALLOW_LIVE": "false", "SCOPESHIELD_E2E_LIVE":"0",
           "SCOPESHIELD_E2E_LIVE_AI":"0", "SCOPESHIELD_E2E_DAY19":"1" if args.day19 else "0",
           "NEXT_TELEMETRY_DISABLED": "1", "SCOPESHIELD_E2E_PASSWORD": password,
           "SCOPESHIELD_E2E_DAY20": "1" if args.day20 else "0",
           "SCOPESHIELD_E2E_DAY24": "1" if args.day24 else "0",
           "SCOPESHIELD_E2E_ARTIFACTS":str(output), "SCOPESHIELD_E2E_DAY10":"1" if args.day10 else "0",
           "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8001"}
    module="scripts.day10_live_app:app" if real_services else "scripts.browser_app:app"
    if real_services:env.update(SCOPESHIELD_E2E_LIVE="1")
    if args.live_day10:
        env.update(SCOPESHIELD_E2E_LIVE_AI="1",SCOPESHIELD_AI_PROVIDER="gemini",SCOPESHIELD_AI_ALLOW_LIVE="true",
            SCOPESHIELD_AI_MODELS="gemini-3.5-flash-lite",SCOPESHIELD_AI_FREE_TIER_MODELS="gemini-3.5-flash-lite",SCOPESHIELD_AI_MAX_MODEL_CALLS="1")
    # Exercise the actual local account-provisioning CLI with hidden input supplied
    # in memory. Passwords never enter argv, files, screenshots or tool output.
    with patch.dict(os.environ,{"SCOPESHIELD_DATABASE_URL":env["SCOPESHIELD_DATABASE_URL"]}), redirect_stdout(io.StringIO()):
        accounts = [("qa.freelancer", "freelancer"), ("qa.conversation", "freelancer"), ("qa.evidence", "freelancer"), ("qa.intelligence", "freelancer"), ("qa.other", "freelancer"), ("qa.client", "client")]
        if args.day24: accounts.append(("qa.release", "freelancer"))
        for name, role in accounts:
            with patch.object(sys,"argv",["manage_users","create","--username",name,"--role",role]), patch.object(manage_users,"getpass",return_value=password):
                manage_users.main()
    if args.day19:
        from scripts.day19_sample import prepare
        prepare(db_path, password, "qa.intelligence")
    servers = []
    logs = []
    stop = threading.Event(); watcher = None
    def terminate(process):
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif process.poll() is None: process.terminate(); process.wait(timeout=10)
    def ready(process,url):
        for _ in range(80):
            if process.poll() is not None: break
            try:
                with urllib.request.urlopen(url,timeout=1) as response:
                    if response.status==200:return
            except (OSError,TimeoutError):pass
            time.sleep(.25)
        raise RuntimeError("Local test service did not start; inspect isolated logs")
    try:
        for command, cwd, label, url in [([sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", "8001"], ROOT, "backend", "http://127.0.0.1:8001/health"),
            ([str(runtime), "start"], ROOT / "frontend", "frontend", "http://127.0.0.1:3000")]:
            log = (db_path.parent / (label + ".log")).open("w", encoding="utf-8"); logs.append(log)
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            servers.append(process)
            ready(process,url)
        if args.day10:
            (ROOT/".local"/("day24" if args.day24 else "day10")/"latest.json").write_text(json.dumps({"artifacts":str(output),"database":str(db_path)}),encoding="utf-8")
            def restart_backend():
                while not stop.wait(.1):
                    if not (output/"restart-request.json").exists():continue
                    before=servers[0].pid
                    try:
                        terminate(servers[0])
                        servers[0]=subprocess.Popen([sys.executable,"-m","uvicorn",module,"--host","127.0.0.1","--port","8001"],
                            cwd=ROOT,env=env,stdout=logs[0],stderr=logs[0],creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
                        ready(servers[0],"http://127.0.0.1:8001/health")
                        info={"restarted":True,"previous_pid":before,"new_pid":servers[0].pid}
                    except Exception as exc:info={"restarted":False,"error_type":type(exc).__name__}
                    (output/"restart-complete.json").write_text(json.dumps(info),encoding="utf-8")
                    return
            watcher=threading.Thread(target=restart_backend,daemon=True); watcher.start()
        result = subprocess.Popen([str(runtime), "exec", "playwright", "test",*(["day10.spec.ts"] if real_services else ["studio.spec.ts"] if args.studio_only else ["day24.spec.ts"] if args.day24_only else [])], cwd=ROOT / "frontend", env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        for line in result.stdout:
            print(line.replace(password, "[redacted]"), end="", flush=True)
        return result.wait()
    finally:
        stop.set()
        if watcher:watcher.join(30)
        for process in reversed(servers):
            terminate(process)
        for log in logs: log.close()
        for path in db_path.parent.rglob("*"):
            if path.suffix in {".json", ".log", ".md"}:
                value = path.read_text(encoding="utf-8", errors="replace")
                if password in value: path.write_text(value.replace(password, "[redacted]"), encoding="utf-8")
        env.pop("SCOPESHIELD_E2E_PASSWORD", None)


if __name__ == "__main__":
    raise SystemExit(main())
