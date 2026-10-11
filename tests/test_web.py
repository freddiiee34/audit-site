import pytest
from fastapi.testclient import TestClient

from app import stockage
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def formulaire(url, email="gerant@exemple.fr", **extra):
    return {"url": url, "email": email, "consentement": "oui"} | extra


def test_parcours_complet(client, site_mauvais):
    rep = client.post("/audit", data=formulaire(site_mauvais), follow_redirects=False)
    assert rep.status_code == 303
    page = client.get(rep.headers["location"])
    assert page.status_code == 200
    assert "Vos 5 priorités" in page.text
    assert "Prendre rendez-vous" in page.text
    assert 'name="robots" content="noindex"' in page.text


def test_accueil(client):
    rep = client.get("/")
    assert rep.status_code == 200 and "Analyser mon site" in rep.text


def test_consentement_obligatoire(client, site_bon):
    rep = client.post("/audit", data={"url": site_bon, "email": "a@b.fr"})
    assert rep.status_code == 400 and "accepter" in rep.text


def test_email_invalide(client, site_bon):
    rep = client.post("/audit", data=formulaire(site_bon, email="pas-un-email"))
    assert rep.status_code == 400


def test_champ_piege(client, site_bon):
    rep = client.post("/audit", data=formulaire(site_bon, site_web="spam"))
    assert rep.status_code == 400


def test_limite_par_email(client, site_bon, monkeypatch):
    monkeypatch.setattr(stockage, "LIMITE_PAR_EMAIL", 2)
    for _ in range(2):
        assert client.post("/audit", data=formulaire(site_bon), follow_redirects=False).status_code == 303
    rep = client.post("/audit", data=formulaire(site_bon))
    assert rep.status_code == 429


def test_site_injoignable_message_lisible(client, site_bon):
    rep = client.post("/audit", data=formulaire(site_bon + "absent.html"))
    assert rep.status_code == 422 and "erreur 404" in rep.text


def test_rapport_inconnu(client):
    assert client.get("/rapport/" + "0" * 32).status_code == 404
    assert client.get("/rapport/../../etc").status_code == 404


def test_ip_stockee_hachee(client, site_bon):
    client.post("/audit", data=formulaire(site_bon, email="hash@exemple.fr"))
    with stockage.connexion() as con:
        ip_hash = con.execute("SELECT ip_hash FROM audits WHERE email = 'hash@exemple.fr'").fetchone()[0]
    assert "testclient" not in ip_hash and len(ip_hash) == 32
