"""Fresh developer tooling must not depend on a Codex/Windows cache path."""
import sys
import pytest
from scripts.tooling import pnpm_executable


def test_pnpm_uses_path_and_refuses_missing_explicit_executable(tmp_path, monkeypatch):
    monkeypatch.delenv('SCOPESHIELD_PNPM', raising=False)
    monkeypatch.setattr('scripts.tooling.shutil.which', lambda name: '/developer/bin/pnpm')
    assert pnpm_executable() == '/developer/bin/pnpm'
    monkeypatch.setenv('SCOPESHIELD_PNPM', str(tmp_path/'missing-pnpm'))
    with pytest.raises(RuntimeError, match='installed executable'): pnpm_executable()


def test_pnpm_missing_path_refuses_without_downloading(monkeypatch):
    monkeypatch.delenv('SCOPESHIELD_PNPM', raising=False)
    monkeypatch.setattr('scripts.tooling.shutil.which', lambda name: None)
    with pytest.raises(RuntimeError, match='Install pnpm'): pnpm_executable()


def test_release_browser_runner_rejects_live_flags_before_creating_fixtures(monkeypatch):
    from scripts import run_browser_tests
    monkeypatch.setattr(sys, 'argv', ['runner', '--day24', '--live-paypal-day10'])
    with pytest.raises(SystemExit) as exc: run_browser_tests.main()
    assert exc.value.code == 2
