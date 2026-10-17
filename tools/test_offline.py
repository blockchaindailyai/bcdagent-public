"""Run prototype tests with credentials stripped and external execution blocked."""
import os
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
os.chdir(root)
sys.path[:0]=[str(root),str(root/'scraper_bot/src')]
for key in list(os.environ):
    if any(word in key.upper() for word in ('TOKEN','SECRET','PRIVATE_KEY','PASSWORD','API_KEY')) or key.startswith(('OPENCLAW_','INGEST_')):
        os.environ.pop(key,None)
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
def offline_guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen','os.system'):
        raise RuntimeError('Offline tests prohibit network and subprocess execution')
sys.addaudithook(offline_guard)
import pytest
raise SystemExit(pytest.main(['-q','tests','scraper_bot/tests','--tb=short','-p','no:cacheprovider']))
