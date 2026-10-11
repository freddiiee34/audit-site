import asyncio
import json
from types import SimpleNamespace

import pytest

from app.analyse import AuditImpossible, auditer
from app.checks import robots_bloque_tout
from app.fetcher import URLRefusee, normaliser_url
from app.rapport import rapport_sans_ia, rediger


def lancer(url):
    return asyncio.run(auditer(url))


def statuts(audit):
    return {r.id: r.statut for r in audit.resultats}


# --- Moteur d'analyse --------------------------------------------------------

def test_bon_site_obtient_un_bon_score(site_bon):
    audit = lancer(site_bon)
    s = statuts(audit)
    assert s["seo.titre"] == "ok"
    assert s["seo.description"] == "ok"
    assert s["mobile.viewport"] == "ok"
    assert s["conformite.mentions"] == "ok"
    assert s["conformite.cookies"] == "ok"  # Analytics présent, mais Axeptio aussi
    assert s["seo.liens"] == "ok"
    assert s["seo.robots"] == "ok" and s["seo.sitemap"] == "ok"
    # Le site de test est en http : la sécurité est le seul vrai point faible.
    assert s["securite.https"] == "probleme"
    assert audit.score >= 75


def test_mauvais_site_remonte_les_vrais_problemes(site_mauvais):
    audit = lancer(site_mauvais)
    s = statuts(audit)
    assert s["seo.titre"] == "probleme"
    assert s["seo.description"] == "probleme"
    assert s["seo.h1"] == "attention"
    assert s["seo.indexable"] == "probleme"
    assert s["mobile.viewport"] == "probleme"
    assert s["conformite.cookies"] == "probleme"
    assert s["conformite.mentions"] == "probleme"
    assert s["accessibilite.alt"] == "probleme"
    assert s["accessibilite.formulaires"] == "attention"
    assert s["seo.liens"] == "probleme"
    assert "page-supprimee" in next(r for r in audit.resultats if r.id == "seo.liens").constat
    assert audit.score < 40
    # Les priorités sont les problèmes les plus lourds, au maximum 5.
    assert len(audit.priorites) == 5
    assert all(p.statut == "probleme" and p.impact == 3 for p in audit.priorites)


def test_cookies_sans_traceur_est_neutre():
    from bs4 import BeautifulSoup
    from app.checks import controles_page
    html = "<html lang=fr><title>Un titre suffisamment long</title><body></body></html>"
    res = controles_page(BeautifulSoup(html, "html.parser"), html, "https://x.fr/", {}, 0.2, 1000)
    assert next(r for r in res if r.id == "conformite.cookies").statut == "info"


def test_site_inexistant_donne_un_message_clair(site_bon):
    with pytest.raises(AuditImpossible):
        lancer(site_bon + "nexiste-pas.html")


# --- Sécurité ----------------------------------------------------------------

def test_adresses_internes_refusees(monkeypatch):
    monkeypatch.delenv("AUDIT_ALLOW_PRIVATE")
    for url in ["http://127.0.0.1/", "http://localhost:8000", "http://169.254.169.254/latest/meta-data",
                "http://10.0.0.5", "http://[::1]/"]:
        with pytest.raises(URLRefusee):
            lancer(url)


def test_normalisation_url():
    assert normaliser_url("monsite.fr") == "https://monsite.fr"
    with pytest.raises(URLRefusee):
        normaliser_url("ftp://monsite.fr")
    with pytest.raises(URLRefusee):
        normaliser_url("   ")


@pytest.mark.parametrize("texte,attendu", [
    ("User-agent: *\nDisallow: /", True),
    ("User-agent: *\nDisallow: /wp-admin/\n\nUser-agent: MauvaisBot\nDisallow: /", False),
    ("User-agent: Googlebot\nDisallow: /", True),
    ("User-agent: *\nAllow: /\n# Disallow: /", False),
    ("", False),
])
def test_robots(texte, attendu):
    assert robots_bloque_tout(texte) is attendu


# --- Rédaction du rapport ----------------------------------------------------

class FauxClient:
    def __init__(self, reponse=None, erreur=None):
        self.reponse, self.erreur = reponse, erreur
        self.messages = self

    def create(self, **kwargs):
        if self.erreur:
            raise self.erreur
        texte = self.reponse if isinstance(self.reponse, str) else json.dumps(self.reponse)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=texte)])


def test_rapport_ia_valide_et_filtre_les_inventions(site_mauvais):
    audit = lancer(site_mauvais)
    vraie = audit.priorites[0].id
    reponse = {
        "resume": "Votre site a plusieurs problèmes importants.",
        "priorites": [
            {"id": "seo.problème_invente", "titre": "Inventé", "pourquoi": "x", "comment": "y"},
            {"id": vraie, "titre": "Titre IA", "pourquoi": "Pourquoi IA", "comment": "Comment IA"},
        ],
        "points_forts": ["Le site répond vite."],
    }
    rapport = rediger(audit, client=FauxClient("```json\n" + json.dumps(reponse) + "\n```"))
    assert rapport.redige_par_ia
    ids = [p.id for p in rapport.priorites]
    assert "seo.problème_invente" not in ids
    assert ids == [p.id for p in audit.priorites]  # rien de perdu, ordre conservé
    assert rapport.priorites[0].titre == "Titre IA"


def test_rapport_repli_si_ia_en_panne(site_mauvais):
    audit = lancer(site_mauvais)
    rapport = rediger(audit, client=FauxClient(erreur=RuntimeError("API indisponible")))
    assert not rapport.redige_par_ia
    assert len(rapport.priorites) == 5
    assert rapport.resume.startswith(f"Votre site obtient {audit.score}/100")


def test_rapport_repli_si_reponse_illisible(site_mauvais):
    audit = lancer(site_mauvais)
    assert not rediger(audit, client=FauxClient("Désolé, je ne peux pas.")).redige_par_ia


def test_chaque_controle_a_un_enjeu(site_bon, site_mauvais):
    from app.checks import ENJEUX
    ids = {r.id for url in (site_bon, site_mauvais) for r in lancer(url).resultats}
    assert ids <= set(ENJEUX), ids - set(ENJEUX)


def test_rapport_sans_ia_explique_l_enjeu(site_mauvais):
    audit = lancer(site_mauvais)
    rapport = rapport_sans_ia(audit)
    p = rapport.priorites[0]
    assert p.pourquoi != audit.priorites[0].constat  # le constat ET l'enjeu
    assert "voici les 5 plus urgents" in rapport.resume


def test_rapport_sans_cle(site_bon):
    audit = lancer(site_bon)
    rapport = rediger(audit)
    assert rapport == rapport_sans_ia(audit)
