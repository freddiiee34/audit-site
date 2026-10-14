import functools
import http.server
import threading
from pathlib import Path

import pytest

SITES = Path(__file__).parent / "sites"


class _Silencieux(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _servir(nom):
    # Un serveur local par site : robots.txt et sitemap sont cherchés à la racine.
    handler = functools.partial(_Silencieux, directory=str(SITES / nom))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture(scope="session")
def site_bon():
    httpd = _servir("bon")
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture(scope="session")
def site_mauvais():
    httpd = _servir("mauvais")
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown()


@pytest.fixture(autouse=True)
def environnement(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_ALLOW_PRIVATE", "1")
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "test.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for var in ("BREVO_API_KEY", "EXPEDITEUR_EMAIL", "NOTIF_EMAIL", "URL_PUBLIQUE", "LIEN_AGENDA"):
        monkeypatch.delenv(var, raising=False)
