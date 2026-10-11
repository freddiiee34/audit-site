"""Orchestration de l'audit : récupère le site, lance les contrôles, calcule le score."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from . import checks
from .checks import ATTENTION, CATEGORIES, INFO, OK, PROBLEME, Resultat
from .fetcher import URLRefusee, lire_texte, normaliser_url, nouveau_client, recuperer_page, statut_url

VALEUR = {OK: 1.0, ATTENTION: 0.5, PROBLEME: 0.0}
POIDS_CATEGORIE = {
    "securite": 1.0,
    "performance": 1.0,
    "seo": 1.2,
    "mobile": 1.0,
    "conformite": 1.2,
    "accessibilite": 0.6,
}


class AuditImpossible(Exception):
    """Le site n'a pas pu être analysé ; le message est destiné au visiteur."""


@dataclass
class Audit:
    url: str
    url_finale: str
    date: str
    score: int
    scores_categories: dict[str, int]
    resultats: list[Resultat]
    priorites: list[Resultat] = field(default_factory=list)

    def dict(self) -> dict:
        return {
            "url": self.url,
            "url_finale": self.url_finale,
            "date": self.date,
            "score": self.score,
            "scores_categories": self.scores_categories,
            "resultats": [r.dict() for r in self.resultats],
            "priorites": [r.id for r in self.priorites],
        }


def calculer_scores(resultats: list[Resultat]) -> tuple[int, dict[str, int]]:
    """Score par catégorie (moyenne pondérée par l'impact) et score global sur 100."""
    par_cat: dict[str, int] = {}
    total, poids_total = 0.0, 0.0
    for cat in CATEGORIES:
        notes = [(VALEUR[r.statut], r.impact) for r in resultats if r.categorie == cat and r.statut in VALEUR]
        if not notes:
            continue
        score = sum(v * i for v, i in notes) / sum(i for _, i in notes)
        par_cat[cat] = round(score * 100)
        total += score * POIDS_CATEGORIE[cat]
        poids_total += POIDS_CATEGORIE[cat]
    global_ = round(total / poids_total * 100) if poids_total else 0
    return global_, par_cat


def choisir_priorites(resultats: list[Resultat], n: int = 5) -> list[Resultat]:
    """Les problèmes d'abord, puis les points d'attention, par impact décroissant."""
    gravite = {PROBLEME: 2, ATTENTION: 1}
    candidats = [r for r in resultats if r.statut in gravite]
    candidats.sort(key=lambda r: (gravite[r.statut] * 3 + r.impact), reverse=True)
    return candidats[:n]


async def auditer(url_brute: str, client: httpx.AsyncClient | None = None) -> Audit:
    url = normaliser_url(url_brute)
    proprietaire = client is None
    client = client or nouveau_client()
    try:
        try:
            page = await recuperer_page(client, url)
        except URLRefusee:
            raise
        except httpx.TooManyRedirects as exc:
            raise AuditImpossible("Le site redirige en boucle : impossible de l'afficher.") from exc
        except httpx.HTTPError as exc:
            # Pas de https ? On retente en http, beaucoup de petits sites en sont là.
            if url.startswith("https://") and "://" not in url_brute.strip():
                try:
                    url = "http://" + url[len("https://"):]
                    page = await recuperer_page(client, url)
                except httpx.HTTPError:
                    raise AuditImpossible("Le site ne répond pas. Vérifiez l'adresse et réessayez.") from exc
            else:
                raise AuditImpossible("Le site ne répond pas. Vérifiez l'adresse et réessayez.") from exc

        if page.statut >= 400:
            raise AuditImpossible(f"Le site a renvoyé une erreur {page.statut} : la page d'accueil n'est pas accessible.")
        if "html" not in page.entetes.get("content-type", "text/html"):
            raise AuditImpossible("Cette adresse ne renvoie pas une page web.")

        soup = BeautifulSoup(page.html, "html.parser")
        resultats = checks.controles_page(soup, page.html, page.url_finale, page.entetes,
                                          page.duree_s, page.poids_octets)
        redirection = checks.controle_redirection_https(url, page.url_finale)
        if redirection:
            resultats.append(redirection)

        racine = f"{urlparse(page.url_finale).scheme}://{urlparse(page.url_finale).netloc}"
        liens = checks.liens_internes(soup, page.url_finale, limite=20)
        (st_robots, txt_robots), st_sitemap, *statuts = await asyncio.gather(
            lire_texte(client, racine + "/robots.txt"),
            statut_url(client, racine + "/sitemap.xml"),
            *(statut_url(client, l) for l in liens),
        )
        resultats += checks.controle_robots_sitemap(st_robots, txt_robots, st_sitemap)
        resultats.append(checks.controle_liens(dict(zip(liens, statuts))))
    finally:
        if proprietaire:
            await client.aclose()

    ordre = list(CATEGORIES)
    resultats.sort(key=lambda r: ordre.index(r.categorie))
    score, par_cat = calculer_scores(resultats)
    return Audit(
        url=url,
        url_finale=page.url_finale,
        date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        score=score,
        scores_categories=par_cat,
        resultats=resultats,
        priorites=choisir_priorites(resultats),
    )


__all__ = ["Audit", "AuditImpossible", "auditer", "calculer_scores", "choisir_priorites", "INFO"]
