"""Récupération sécurisée des pages à analyser.

Le service est public : n'importe qui peut soumettre une URL. On bloque donc
les adresses internes (localhost, réseau privé, métadonnées cloud) pour éviter
qu'on se serve de l'outil pour sonder notre propre infrastructure (SSRF).
Chaque redirection est revérifiée.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

USER_AGENT = "AuditSiteBot/1.0 (+audit de site gratuit pour PME)"
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
MAX_HTML_BYTES = 5 * 1024 * 1024  # au-delà, on arrête de lire


class URLRefusee(ValueError):
    """URL invalide ou pointant vers une adresse interdite."""


def _private_allowed() -> bool:
    # Uniquement pour les tests locaux. Ne jamais activer en production.
    return os.getenv("AUDIT_ALLOW_PRIVATE") == "1"


def normaliser_url(brute: str) -> str:
    brute = (brute or "").strip()
    if not brute:
        raise URLRefusee("Merci d'indiquer l'adresse de votre site.")
    if "://" not in brute:
        brute = "https://" + brute
    parsed = urlparse(brute)
    if parsed.scheme not in ("http", "https"):
        raise URLRefusee("Seules les adresses http et https sont acceptées.")
    if not parsed.hostname:
        raise URLRefusee("Cette adresse ne semble pas valide.")
    return brute


def verifier_hote(hostname: str) -> None:
    """Refuse les hôtes qui résolvent vers une adresse non publique."""
    if _private_allowed():
        return
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise URLRefusee(f"Le domaine « {hostname} » est introuvable.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise URLRefusee("Cette adresse ne peut pas être analysée.")


async def _hook_requete(request: httpx.Request) -> None:
    # Appelé à chaque requête, redirections comprises.
    if request.url.scheme not in ("http", "https"):
        raise URLRefusee("Redirection vers un protocole non autorisé.")
    # La résolution DNS est bloquante : on la sort de la boucle asynchrone.
    await asyncio.to_thread(verifier_hote, request.url.host)


def nouveau_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=TIMEOUT,
        follow_redirects=True,
        max_redirects=5,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
        event_hooks={"request": [_hook_requete]},
    )


@dataclass
class PageRecuperee:
    url_demandee: str
    url_finale: str
    statut: int
    duree_s: float
    poids_octets: int
    html: str
    entetes: dict[str, str] = field(default_factory=dict)
    redirections: list[str] = field(default_factory=list)


async def recuperer_page(client: httpx.AsyncClient, url: str) -> PageRecuperee:
    debut = time.perf_counter()
    async with client.stream("GET", url) as resp:
        morceaux: list[bytes] = []
        total = 0
        async for morceau in resp.aiter_bytes():
            total += len(morceau)
            if total > MAX_HTML_BYTES:
                break
            morceaux.append(morceau)
        duree = time.perf_counter() - debut
        brut = b"".join(morceaux)
        encodage = resp.encoding or "utf-8"
        try:
            html = brut.decode(encodage, errors="replace")
        except LookupError:
            html = brut.decode("utf-8", errors="replace")
        return PageRecuperee(
            url_demandee=url,
            url_finale=str(resp.url),
            statut=resp.status_code,
            duree_s=duree,
            poids_octets=total,
            html=html,
            entetes={k.lower(): v for k, v in resp.headers.items()},
            redirections=[str(r.url) for r in resp.history],
        )


async def statut_url(client: httpx.AsyncClient, url: str) -> int | None:
    """Code HTTP d'une URL (HEAD, puis GET si le serveur refuse HEAD)."""
    try:
        resp = await client.head(url)
        if resp.status_code in (403, 405, 501):
            resp = await client.get(url)
        return resp.status_code
    except (httpx.HTTPError, URLRefusee):
        return None


async def lire_texte(client: httpx.AsyncClient, url: str) -> tuple[int | None, str]:
    try:
        resp = await client.get(url)
        return resp.status_code, resp.text[:200_000]
    except (httpx.HTTPError, URLRefusee):
        return None, ""
