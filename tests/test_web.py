import pytest
from fastapi.testclient import TestClient

from app import stockage
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def analyser(client, url, **extra):
    rep = client.post("/audit", data={"url": url} | extra, follow_redirects=False)
    assert rep.status_code == 303, rep.text[:300]
    return rep.headers["location"].rsplit("/", 1)[-1]


def debloquer(client, ident, email="gerant@exemple.fr", **extra):
    data = {"email": email, "consentement": "oui"} | extra
    return client.post(f"/rapport/{ident}/email", data=data, follow_redirects=False)


def test_score_visible_avant_email(client, site_mauvais):
    ident = analyser(client, site_mauvais)
    page = client.get(f"/rapport/{ident}").text
    assert "/100" in page and "Vos 5 priorités" in page
    assert "Recevoir le rapport" in page
    assert "Pourquoi c'est important" not in page and "Le détail des contrôles" not in page
    assert 'name="robots" content="noindex"' in page
    with stockage.connexion() as con:
        assert con.execute("SELECT email FROM audits WHERE id = ?", (ident,)).fetchone()[0] == ""


def test_email_debloque_le_rapport_complet(client, site_mauvais):
    ident = analyser(client, site_mauvais)
    rep = debloquer(client, ident)
    assert rep.status_code == 303 and rep.headers["location"].endswith("#priorites")
    page = client.get(f"/rapport/{ident}").text
    assert "Pourquoi c'est important" in page and "Le détail des contrôles" in page
    assert "Recevoir le rapport" not in page
    assert "Demander un devis" in page and "Réserver l'appel" in page


def test_email_deja_associe_non_remplacable(client, site_bon):
    ident = analyser(client, site_bon)
    debloquer(client, ident, email="premier@exemple.fr")
    debloquer(client, ident, email="intrus@exemple.fr")
    assert stockage.charger(ident)["email"] == "premier@exemple.fr"


def test_accueil(client):
    rep = client.get("/")
    assert rep.status_code == 200 and "Analyser mon site" in rep.text
    assert "<title>Audit gratuit de site internet en 1 minute · Simon Delrue, Lille</title>" in rep.text
    assert "Développeur web &amp; IA pour les PME" in rep.text
    for texte in ('href="/mentions-legales"', 'href="/confidentialite"', "Vraiment gratuit", "Que faites-vous de mon email", "si je n'y connais rien"):
        assert texte.lower() in rep.text.lower()
    assert "fonts.googleapis" not in rep.text  # aucune ressource tierce


def test_champ_piege_invisible_pour_tous(client):
    page = client.get("/").text
    bloc = page[page.index('class="piege"') - 20:page.index('id="site_web"') + 80]
    assert 'aria-hidden="true"' in bloc and 'tabindex="-1"' in bloc and 'autocomplete="off"' in bloc


def test_compteur_affiche_seulement_au_dela_du_seuil(client, monkeypatch):
    monkeypatch.setattr(stockage, "nombre_audits", lambda: 7)
    assert "sites déjà analysés" not in client.get("/").text
    monkeypatch.setattr(stockage, "nombre_audits", lambda: 1234)
    assert "sites déjà analysés" in client.get("/").text


def test_avis_masques_tant_qu_il_n_y_en_a_pas(client):
    assert "Ils m'ont fait confiance" not in client.get("/").text


@pytest.mark.parametrize("chemin,texte", [
    ("/mentions-legales", "OVH SAS"),
    ("/confidentialite", "CNIL"),
    ("/robots.txt", "Sitemap:"),
    ("/sitemap.xml", "<urlset"),
    ("/static/logo.svg", "<svg"),
    ("/realisations", "réalisé en agence"),
])
def test_pages_annexes(client, chemin, texte):
    rep = client.get(chemin)
    assert rep.status_code == 200 and texte in rep.text


def test_mentions_avec_infos_editeur(client, monkeypatch):
    monkeypatch.setenv("EDITEUR_SIRET", "123 456 789 00012")
    monkeypatch.setenv("CONTACT_EMAIL", "contact@simondelrue.fr")
    page = client.get("/mentions-legales").text
    assert "123 456 789 00012" in page and "contact@simondelrue.fr" in page


def test_consentement_obligatoire(client, site_bon):
    ident = analyser(client, site_bon)
    rep = client.post(f"/rapport/{ident}/email", data={"email": "a@b.fr"})
    assert rep.status_code == 400 and "accepter" in rep.text


def test_email_invalide(client, site_bon):
    ident = analyser(client, site_bon)
    assert debloquer(client, ident, email="pas-un-email").status_code == 400


def test_champ_piege(client, site_bon):
    rep = client.post("/audit", data={"url": site_bon, "site_web": "spam"})
    assert rep.status_code == 400
    ident = analyser(client, site_bon)
    assert debloquer(client, ident, site_web="spam").status_code == 400


def test_url_vide(client):
    rep = client.post("/audit", data={"url": " "})
    assert rep.status_code == 400 and "adresse" in rep.text


def test_limite_par_ip(client, site_bon, monkeypatch):
    monkeypatch.setattr(stockage, "LIMITE_PAR_IP", 2)
    for _ in range(2):
        analyser(client, site_bon)
    assert client.post("/audit", data={"url": site_bon}).status_code == 429


def test_limite_par_email(client, site_bon, monkeypatch):
    monkeypatch.setattr(stockage, "LIMITE_PAR_EMAIL", 2)
    for _ in range(2):
        assert debloquer(client, analyser(client, site_bon)).status_code == 303
    assert debloquer(client, analyser(client, site_bon)).status_code == 429


def test_site_injoignable_message_lisible(client, site_bon):
    rep = client.post("/audit", data={"url": site_bon + "absent.html"})
    assert rep.status_code == 422 and "erreur 404" in rep.text


def test_rapport_inconnu(client):
    assert client.get("/rapport/" + "0" * 32).status_code == 404
    assert client.get("/rapport/../../etc").status_code == 404
    assert client.post("/rapport/" + "0" * 32 + "/email", data={"email": "a@b.fr"}).status_code == 404


def test_ip_derriere_proxy_non_falsifiable(monkeypatch):
    from types import SimpleNamespace
    from app.main import ip_client
    monkeypatch.setenv("DERRIERE_PROXY", "1")
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9"}, client=None)
    assert ip_client(req) == "203.0.113.9"  # l'IP ajoutée par le proxy, pas celle inventée


def test_ip_stockee_hachee(client, site_bon):
    ident = analyser(client, site_bon)
    with stockage.connexion() as con:
        ip_hash = con.execute("SELECT ip_hash FROM audits WHERE id = ?", (ident,)).fetchone()[0]
    assert "testclient" not in ip_hash and len(ip_hash) == 32


def test_realisations_sans_nom_d_agence(client):
    page = client.get("/realisations").text
    assert "caribou" not in page.lower()
    for cle in ("happy-hours", "emailing-sante", "galerie-ia"):
        assert client.get(f"/static/realisations/{cle}-bureau.webp").status_code == 200
    assert 'href="/realisations"' in client.get("/").text


def test_theme_alternatif(client, site_mauvais, monkeypatch):
    monkeypatch.setenv("THEME", "classique")
    assert "/static/style.css" in client.get("/").text
    monkeypatch.delenv("THEME")
    page = client.get("/").text
    assert "style-alt.css" in page and 'data-theme="alt"' in page and "inter.woff2" not in page
    ident = analyser(client, site_mauvais)
    assert "Défaillance" in client.get(f"/rapport/{ident}").text
