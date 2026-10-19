"""Locate an installed tool without a workstation-specific cache or download."""
import os
from pathlib import Path
import shutil


def pnpm_executable():
    explicit = os.environ.get('SCOPESHIELD_PNPM')
    if explicit:
        path = Path(explicit)
        if not path.is_file(): raise RuntimeError('SCOPESHIELD_PNPM must name an installed executable')
        return str(path.resolve())
    path = shutil.which('pnpm')
    if not path: raise RuntimeError('Install pnpm 11.25.0 on PATH or set SCOPESHIELD_PNPM to its executable')
    return path
