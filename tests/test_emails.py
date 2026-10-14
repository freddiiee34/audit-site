"""Emails : rapport, alerte de nouvel audit, demande de rendez-vous (Brevo simulé)."""

import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import envoi, stockage
from app.main import app


class FauxBrevo:
    def __init__(self, statut=201):
        self.envois, self.statut = [], statut

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.envois.append(json)
        return httpx.Response(self.statut, json={"messageId": "x"}, request=httpx.Request("POST", url))

    def a(self, destinataire):
        return [m for m in self.envois if m["to"][0]["email"] == destinataire]


@pytest.fixture
def brevo(monkeypatch):
    faux = FauxBrevo()
    monkeypatch.setattr(envoi.httpx, "post", faux)
    monkeypatch.setenv("BREVO_API_KEY", "cle-test")
    monkeypatch.setenv("EXPEDITEUR_EMAIL", "audit@simondelrue.fr")
    monkeypatch.setenv("NOTIF_EMAIL", "simondelruepro@gmail.com")
    monkeypatch.setenv("URL_PUBLIQUE", "https://audit.simondelrue.fr")
    return faux


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def lancer_audit(client, site, email="gerant@exemple.fr"):
    rep = client.post("/audit", data={"url": site, "email": email, "consentement": "oui"}, follow_redirects=False)
    assert rep.status_code == 303
    return rep.headers["location"].rsplit("/", 1)[-1]


RDV = {"nom": "Marie Martin", "entreprise": "Boulangerie Martin", "email": "marie@exemple.fr",
       "telephone": "06 12 34 56 78", "disponibilites": "jeudi matin", "message": "Le site est lent <b>sur mobile</b>"}


def test_audit_envoie_rapport_et_alerte(client, site_mauvais, brevo):
    ident = lancer_audit(client, site_mauvais)
    rapport, = brevo.a("gerant@exemple.fr")
    alerte, = brevo.a("simondelruepro@gmail.com")
    lien = f"https://audit.simondelrue.fr/rapport/{ident}"
    assert lien in rapport["htmlContent"] and lien in alerte["htmlContent"]
    assert alerte["replyTo"]["email"] == "gerant@exemple.fr"  # répondre = écrire au prospect
    assert rapport["replyTo"]["email"] == "simondelruepro@gmail.com"
    with stockage.connexion() as con:
        assert con.execute("SELECT email_envoye FROM audits WHERE id = ?", (ident,)).fetchone()[0] == 1


def test_bouton_rdv_mene_au_formulaire(client, site_bon):
    ident = lancer_audit(client, site_bon)
    page = client.get(f"/rapport/{ident}").text
    assert f'href="/rendez-vous/{ident}"' in page
    formulaire = client.get(f"/rendez-vous/{ident}")
    assert formulaire.status_code == 200
    assert 'value="gerant@exemple.fr"' in formulaire.text  # email prérempli


def test_demande_rdv_notifie_simon_avec_toutes_les_infos(client, site_mauvais, brevo):
    ident = lancer_audit(client, site_mauvais)
    brevo.envois.clear()
    rep = client.post(f"/rendez-vous/{ident}", data=RDV)
    assert rep.status_code == 200 and "Merci, Marie Martin" in rep.text

    notif, = brevo.a("simondelruepro@gmail.com")
    corps = notif["htmlContent"]
    for valeur in ["Marie Martin", "Boulangerie Martin", "marie@exemple.fr", "06 12 34 56 78", "jeudi matin"]:
        assert valeur in corps
    assert "&lt;b&gt;sur mobile&lt;/b&gt;" in corps and "<b>sur mobile</b>" not in corps  # pas d'injection HTML
    assert f"/rapport/{ident}" in corps
    assert notif["replyTo"]["email"] == "marie@exemple.fr"
    assert "Marie Martin" in notif["subject"]

    confirmation, = brevo.a("marie@exemple.fr")
    assert confirmation["subject"] == "Votre demande de rendez-vous"

    with stockage.connexion() as con:
        ligne = con.execute("SELECT nom, notifie FROM rendez_vous WHERE audit_id = ?", (ident,)).fetchone()
    assert tuple(ligne) == ("Marie Martin", 1)


def test_rdv_enregistre_meme_sans_brevo(client, site_bon):
    ident = lancer_audit(client, site_bon)
    rep = client.post(f"/rendez-vous/{ident}", data=RDV)
    assert rep.status_code == 200 and "confirmation vous a été envoyée" not in rep.text
    assert stockage.nombre_rdv(ident) == 1


@pytest.mark.parametrize("modif,attendu", [
    ({"nom": ""}, "votre nom"),
    ({"email": "pas-un-email"}, "email valide"),
    ({"telephone": "appelez-moi"}, "téléphone"),
    ({"site_web": "spam"}, "pas pu être traitée"),
])
def test_rdv_validation(client, site_bon, modif, attendu):
    ident = lancer_audit(client, site_bon)
    rep = client.post(f"/rendez-vous/{ident}", data=RDV | modif)
    assert rep.status_code == 400 and attendu in rep.text
    assert stockage.nombre_rdv(ident) == 0


def test_rdv_limite_par_audit(client, site_bon):
    ident = lancer_audit(client, site_bon)
    for _ in range(stockage.LIMITE_RDV_PAR_AUDIT):
        assert client.post(f"/rendez-vous/{ident}", data=RDV).status_code == 200
    assert client.post(f"/rendez-vous/{ident}", data=RDV).status_code == 429


def test_rdv_audit_inconnu(client):
    assert client.get("/rendez-vous/" + "a" * 32).status_code == 404
    assert client.post("/rendez-vous/../etc", data=RDV).status_code == 404


def test_refus_brevo_journalise(monkeypatch, caplog):
    faux = FauxBrevo(statut=401)
    monkeypatch.setattr(envoi.httpx, "post", faux)
    monkeypatch.setenv("BREVO_API_KEY", "mauvaise")
    monkeypatch.setenv("EXPEDITEUR_EMAIL", "audit@simondelrue.fr")
    assert envoi.envoyer("a@b.fr", "Test", "<p>x</p>") is False
    assert re.search(r"Brevo a refusé.*401", caplog.text)
