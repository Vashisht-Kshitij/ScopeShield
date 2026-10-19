"""Isolated real-service UI verification only; approvals remain outside HTTP."""
import os
from pathlib import Path
import requests
from app.main import create_app
from app.dependencies import get_paypal
from app.database import build_engine,database_url
from scripts.day10_guard import Day10NetworkGuard,matches_committed_reservation

root=Path(__file__).resolve().parents[1]
directory=Path(os.environ["SCOPESHIELD_E2E_ARTIFACTS"]).resolve()
if os.environ.get("SCOPESHIELD_E2E_LIVE")!="1" or not directory.is_relative_to(root/".local/day10"):
    raise RuntimeError("Use only the isolated Day 10 verification runner")
def reservation_check(plan,options):
    engine=build_engine(database_url())
    try:
        return matches_committed_reservation(engine,plan,options)
    finally:engine.dispose()
guard=Day10NetworkGuard(directory,requests.sessions.Session.request,get_paypal().recipient_email,reservation_check)
requests.sessions.Session.request=lambda session,method,url,**kwargs:guard.request(session,method,url,**kwargs)
app=create_app()
