"""Opt-in destructive test-schema runner. Never targets the clinic database."""
import os
import sys
from pathlib import Path
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2] / 'OdontoFlow-Backend-github-clean'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app.config import get_settings

url = make_url(get_settings().test_database_url)
if url.host not in {'localhost', '127.0.0.1'} or url.port != 5434 or url.database != 'odontoflow_test':
    raise SystemExit('Solo se permite localhost:5434/odontoflow_test.')
os.chdir(ROOT)
import pytest
raise SystemExit(pytest.main(['-p', 'conftest', str(Path(__file__).with_name('scenarios.py')),
                             '-q', '-o', 'asyncio_mode=auto', '--tb=short']))
