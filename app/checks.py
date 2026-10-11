"""Contrôles de l'audit.

Chaque contrôle produit un `Resultat` factuel : statut, constat chiffré et
conseil. Ces constats sont la seule matière que l'IA a le droit d'utiliser
pour rédiger le rapport : elle reformule, elle n'invente pas.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

OK, ATTENTION, PROBLEME, INFO = "ok", "attention", "probleme", "info"

CATEGORIES = {
    "securite": "Sécurité",
    "performance": "Vitesse",
    "seo": "Référencement Google",
    "mobile": "Mobile",
    "conformite": "Obligations légales",
    "accessibilite": "Accessibilité",
}


# Ce que chaque problème coûte concrètement à l'entreprise. Utilisé par le
# rapport sans IA, et fourni à l'IA comme base pour expliquer l'enjeu.
ENJEUX = {
    "securite.https": "Chrome affiche « Non sécurisé » à côté de votre adresse : beaucoup de visiteurs partent, et Google favorise les sites en HTTPS.",
    "securite.hsts": "Sans cette protection, une partie des visites peut encore passer par une connexion non chiffrée.",
    "securite.redirection": "Les visiteurs qui tapent l'ancienne adresse arrivent sur une version non sécurisée du site.",
    "performance.reponse": "Plus une page est lente, plus les visiteurs abandonnent avant de la voir, surtout sur mobile.",
    "performance.poids_html": "Une page lourde s'affiche plus lentement, en particulier avec une connexion mobile.",
    "performance.fichiers": "Chaque fichier supplémentaire retarde l'affichage de la page.",
    "performance.lazy": "Le téléphone télécharge des images que le visiteur ne verra peut-être jamais, ce qui ralentit la page.",
    "seo.titre": "C'est le texte cliquable dans les résultats Google : sans lui, Google en invente un et vous perdez des clics.",
    "seo.description": "C'est le petit texte sous votre lien dans Google : bien écrit, il donne envie de cliquer chez vous plutôt que chez un concurrent.",
    "seo.h1": "Le titre principal indique à Google et aux visiteurs le sujet de la page.",
    "seo.indexable": "Votre page peut purement et simplement disparaître des résultats Google.",
    "seo.canonical": "Google peut voir plusieurs adresses pour la même page et diluer votre visibilité.",
    "seo.langue": "Google et les lecteurs d'écran comprennent moins bien dans quelle langue est votre site.",
    "seo.partage": "Quand quelqu'un partage votre site, l'aperçu peut être vide ou mal choisi, ce qui donne moins envie de cliquer.",
    "seo.robots": "Ce fichier indique à Google ce qu'il a le droit d'explorer ; mal réglé, il peut cacher tout votre site.",
    "seo.sitemap": "Google met plus de temps à découvrir vos pages, surtout les nouvelles.",
    "seo.liens": "Un visiteur qui tombe sur une page d'erreur perd confiance, et Google y voit un site mal entretenu.",
    "mobile.viewport": "La majorité des visites se font sur téléphone : un site illisible sur mobile fait fuir ces visiteurs et recule dans Google.",
    "accessibilite.alt": "Les personnes malvoyantes ne savent pas ce que montrent vos images, et Google Images ne peut pas les référencer.",
    "accessibilite.formulaires": "Vos formulaires sont difficiles à remplir pour les personnes qui utilisent un lecteur d'écran.",
    "conformite.mentions": "Les mentions légales sont obligatoires pour un site professionnel en France ; leur absence peut être sanctionnée par une amende.",
    "conformite.confidentialite": "Le RGPD impose d'expliquer ce que vous faites des données collectées, par exemple via un formulaire de contact.",
    "conformite.cookies": "Déposer des cookies de suivi sans accord préalable du visiteur expose à des sanctions de la CNIL.",
}


def format_fr(nombre: float, decimales: int = 1) -> str:
    return f"{nombre:.{decimales}f}".replace(".", ",")


@dataclass
class Resultat:
    id: str
    categorie: str
    titre: str
    statut: str  # ok | attention | probleme | info
    constat: str  # ce qu'on a mesuré, en clair
    conseil: str  # quoi faire si ce n'est pas ok
    impact: int  # 1 (faible) à 3 (fort) : sert à classer les priorités

    def dict(self) -> dict:
        return asdict(self)


# --- Détections utilitaires -------------------------------------------------

TRACKERS = {
    "Google Analytics": ["googletagmanager.com/gtag", "google-analytics.com", "gtag(", "ga('create"],
    "Google Tag Manager": ["googletagmanager.com/gtm.js", "GTM-"],
    "Pixel Meta (Facebook)": ["connect.facebook.net", "fbq("],
    "Hotjar": ["static.hotjar.com", "hj("],
    "LinkedIn Insight": ["snap.licdn.com"],
    "TikTok Pixel": ["analytics.tiktok.com"],
    "Matomo": ["matomo.js", "piwik.js"],
}

CMPS = {
    "Axeptio": ["axeptio"],
    "Didomi": ["didomi"],
    "Cookiebot": ["cookiebot"],
    "OneTrust": ["onetrust", "optanon"],
    "tarteaucitron": ["tarteaucitron"],
    "Complianz": ["complianz", "cmplz"],
    "CookieYes": ["cookieyes", "cookie-law-info"],
    "Sirdata": ["sddan.com", "sirdata"],
    "Usercentrics": ["usercentrics"],
    "Iubenda": ["iubenda"],
    "Quantcast Choice": ["quantcast.mgr.consensu", "quantcast choice"],
    "Borlabs Cookie": ["borlabs-cookie"],
}


def detecter(html_bas: str, catalogue: dict[str, list[str]]) -> list[str]:
    return [nom for nom, motifs in catalogue.items() if any(m.lower() in html_bas for m in motifs)]


def _texte(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def liens_internes(soup: BeautifulSoup, url_base: str, limite: int = 25) -> list[str]:
    hote = urlparse(url_base).hostname
    vus: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolu = urljoin(url_base, href).split("#")[0]
        p = urlparse(absolu)
        if p.scheme in ("http", "https") and p.hostname == hote and absolu not in vus:
            vus.append(absolu)
        if len(vus) >= limite:
            break
    return vus


def _cherche_lien(soup: BeautifulSoup, motifs: list[str]) -> str | None:
    for a in soup.find_all("a", href=True):
        cible = (_texte(a) + " " + a["href"]).lower()
        if any(m in cible for m in motifs):
            return a["href"]
    return None


# --- Contrôles sur la page d'accueil ----------------------------------------


def controles_page(soup: BeautifulSoup, html: str, url_finale: str, entetes: dict,
                   duree_s: float, poids_octets: int) -> list[Resultat]:
    r: list[Resultat] = []
    html_bas = html.lower()

    # Sécurité
    https = url_finale.startswith("https://")
    r.append(Resultat(
        "securite.https", "securite", "Connexion sécurisée (HTTPS)",
        OK if https else PROBLEME,
        "Le site s'affiche en HTTPS." if https else
        "Le site s'affiche sans HTTPS (connexion non chiffrée).",
        "Installer un certificat SSL (souvent gratuit chez l'hébergeur) et rediriger tout le trafic vers HTTPS.",
        3,
    ))
    if https:
        hsts = "strict-transport-security" in entetes
        r.append(Resultat(
            "securite.hsts", "securite", "Protection HSTS",
            OK if hsts else ATTENTION,
            "L'en-tête HSTS est présent." if hsts else "L'en-tête HSTS est absent.",
            "Activer l'en-tête Strict-Transport-Security pour forcer HTTPS sur toutes les visites.",
            1,
        ))

    # Performance
    if duree_s < 0.1:
        st, msg = OK, "La page répond en moins de 0,1 s."
    elif duree_s < 0.8:
        st, msg = OK, f"La page répond en {format_fr(duree_s)} s."
    elif duree_s < 2:
        st, msg = ATTENTION, f"La page met {format_fr(duree_s)} s à répondre (objectif : moins de 0,8 s)."
    else:
        st, msg = PROBLEME, f"La page met {format_fr(duree_s)} s à répondre : une partie des visiteurs part avant."
    r.append(Resultat(
        "performance.reponse", "performance", "Temps de réponse du serveur", st, msg,
        "Activer un cache de pages, vérifier l'hébergement et réduire les extensions qui ralentissent le serveur.",
        3,
    ))

    ko = poids_octets / 1024
    st = OK if ko < 300 else ATTENTION if ko < 1000 else PROBLEME
    r.append(Resultat(
        "performance.poids_html", "performance", "Poids du code de la page", st,
        f"Le code HTML de la page pèse {ko:.0f} Ko.",
        "Alléger le code généré par le constructeur de pages et supprimer les blocs inutilisés.",
        1,
    ))

    scripts = soup.find_all("script", src=True)
    css = soup.find_all("link", rel=lambda v: v and "stylesheet" in v)
    nb = len(scripts) + len(css)
    st = OK if nb <= 20 else ATTENTION if nb <= 40 else PROBLEME
    r.append(Resultat(
        "performance.fichiers", "performance", "Nombre de fichiers chargés", st,
        f"La page charge {len(scripts)} scripts et {len(css)} feuilles de style.",
        "Regrouper et minifier les fichiers (une extension de cache le fait souvent) et retirer les extensions inutiles.",
        2,
    ))

    images = soup.find_all("img")
    if len(images) > 3:
        sans_lazy = [i for i in images[3:] if (i.get("loading") or "").lower() != "lazy"]
        st = OK if not sans_lazy else ATTENTION
        r.append(Resultat(
            "performance.lazy", "performance", "Chargement différé des images", st,
            "Les images hors écran sont chargées en différé." if not sans_lazy else
            f"{len(sans_lazy)} images sur {len(images)} sont chargées immédiatement, même hors écran.",
            "Ajouter loading=\"lazy\" aux images situées plus bas dans la page.",
            1,
        ))

    # SEO
    titre = _texte(soup.title)
    if not titre:
        st, msg = PROBLEME, "La page n'a pas de titre (balise title)."
    elif not 10 <= len(titre) <= 65:
        st, msg = ATTENTION, f"Le titre fait {len(titre)} caractères (idéal : 30 à 65) : « {titre[:80]} »."
    else:
        st, msg = OK, f"Titre de {len(titre)} caractères : « {titre} »."
    r.append(Resultat(
        "seo.titre", "seo", "Titre de la page", st, msg,
        "Écrire un titre de 30 à 65 caractères avec l'activité et la ville, par exemple « Plombier à Lille | Nom ».",
        3,
    ))

    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc = (meta.get("content") or "").strip() if meta else ""
    if not desc:
        st, msg = PROBLEME, "Il n'y a pas de description pour Google (meta description)."
    elif not 50 <= len(desc) <= 160:
        st, msg = ATTENTION, f"La description fait {len(desc)} caractères (idéal : 120 à 160)."
    else:
        st, msg = OK, f"Description de {len(desc)} caractères."
    r.append(Resultat(
        "seo.description", "seo", "Description dans Google", st, msg,
        "Rédiger une description de 120 à 160 caractères qui donne envie de cliquer depuis les résultats Google.",
        2,
    ))

    h1s = soup.find_all("h1")
    if len(h1s) == 1:
        st, msg = OK, f"Un titre principal (H1) : « {_texte(h1s[0])[:80]} »."
    elif not h1s:
        st, msg = PROBLEME, "La page n'a pas de titre principal (H1)."
    else:
        st, msg = ATTENTION, f"La page a {len(h1s)} titres principaux (H1) au lieu d'un seul."
    r.append(Resultat(
        "seo.h1", "seo", "Titre principal (H1)", st, msg,
        "Garder un seul H1 par page, qui dit clairement ce que vous faites.",
        2,
    ))

    robots_meta = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    noindex = bool(robots_meta and "noindex" in (robots_meta.get("content") or "").lower()) \
        or "noindex" in entetes.get("x-robots-tag", "").lower()
    r.append(Resultat(
        "seo.indexable", "seo", "Visibilité dans Google",
        PROBLEME if noindex else OK,
        "La page demande à Google de NE PAS l'afficher (noindex)." if noindex else
        "La page autorise Google à l'afficher.",
        "Retirer la consigne noindex (souvent une case « Demander aux moteurs de ne pas indexer » restée cochée).",
        3,
    ))

    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    r.append(Resultat(
        "seo.canonical", "seo", "Adresse de référence (canonical)",
        OK if canonical and canonical.get("href") else ATTENTION,
        "Une adresse de référence est déclarée." if canonical and canonical.get("href") else
        "Aucune adresse de référence (canonical) n'est déclarée.",
        "Déclarer une balise canonical (les extensions SEO comme Yoast le font automatiquement).",
        1,
    ))

    html_tag = soup.find("html")
    lang = (html_tag.get("lang") if html_tag else None) or ""
    r.append(Resultat(
        "seo.langue", "seo", "Langue déclarée",
        OK if lang else ATTENTION,
        f"Langue déclarée : {lang}." if lang else "La langue de la page n'est pas déclarée.",
        "Ajouter lang=\"fr\" sur la balise html.",
        1,
    ))

    og = soup.find("meta", attrs={"property": "og:image"}) and soup.find("meta", attrs={"property": "og:title"})
    r.append(Resultat(
        "seo.partage", "seo", "Aperçu sur les réseaux sociaux",
        OK if og else ATTENTION,
        "Le titre et l'image de partage sont définis." if og else
        "Quand on partage le site sur Facebook ou LinkedIn, l'aperçu (titre, image) n'est pas maîtrisé.",
        "Définir les balises Open Graph (og:title, og:image), en général via l'extension SEO.",
        1,
    ))

    # Mobile
    viewport = soup.find("meta", attrs={"name": re.compile("^viewport$", re.I)})
    r.append(Resultat(
        "mobile.viewport", "mobile", "Affichage sur mobile",
        OK if viewport else PROBLEME,
        "La page est configurée pour s'adapter aux écrans mobiles." if viewport else
        "La page n'est pas configurée pour les mobiles : le texte s'affiche en tout petit.",
        "Ajouter la balise meta viewport et vérifier que le thème est responsive.",
        3,
    ))

    # Accessibilité
    if images:
        sans_alt = [i for i in images if i.get("alt") is None]
        part = len(sans_alt) / len(images)
        st = OK if not sans_alt else ATTENTION if part <= 0.3 else PROBLEME
        r.append(Resultat(
            "accessibilite.alt", "accessibilite", "Description des images", st,
            "Toutes les images ont une description (alt)." if not sans_alt else
            f"{len(sans_alt)} images sur {len(images)} n'ont pas de description (alt).",
            "Décrire chaque image utile en quelques mots : c'est lu par les malvoyants et par Google Images.",
            2,
        ))

    champs = [c for c in soup.find_all(["input", "select", "textarea"])
              if (c.get("type") or "").lower() not in ("hidden", "submit", "button", "image", "reset")]
    if champs:
        ids_label = {lab.get("for") for lab in soup.find_all("label") if lab.get("for")}
        sans_label = [c for c in champs
                      if not (c.get("id") in ids_label or c.get("aria-label") or c.get("aria-labelledby")
                              or c.find_parent("label"))]
        r.append(Resultat(
            "accessibilite.formulaires", "accessibilite", "Champs de formulaire étiquetés",
            OK if not sans_label else ATTENTION,
            "Tous les champs de formulaire ont une étiquette." if not sans_label else
            f"{len(sans_label)} champs de formulaire sur {len(champs)} n'ont pas d'étiquette lisible.",
            "Associer une étiquette (label) à chaque champ pour les lecteurs d'écran.",
            1,
        ))

    # Obligations légales
    mentions = _cherche_lien(soup, ["mentions légales", "mentions legales", "mentions-legales", "legal-notice",
                                    "mentions_legales", "/legal"])
    r.append(Resultat(
        "conformite.mentions", "conformite", "Mentions légales",
        OK if mentions else PROBLEME,
        "Un lien vers les mentions légales est présent." if mentions else
        "Aucun lien vers les mentions légales n'a été trouvé sur la page d'accueil.",
        "Ajouter une page « Mentions légales » (obligatoire en France) et un lien dans le pied de page.",
        3,
    ))

    confid = _cherche_lien(soup, ["confidentialité", "confidentialite", "données personnelles",
                                  "donnees-personnelles", "privacy", "rgpd"])
    r.append(Resultat(
        "conformite.confidentialite", "conformite", "Politique de confidentialité",
        OK if confid else ATTENTION,
        "Un lien vers la politique de confidentialité est présent." if confid else
        "Aucun lien vers une politique de confidentialité n'a été trouvé.",
        "Publier une politique de confidentialité expliquant l'usage des données collectées (formulaires, cookies).",
        2,
    ))

    traceurs = detecter(html_bas, TRACKERS)
    cmp = detecter(html_bas, CMPS)
    if traceurs and not cmp:
        st = PROBLEME
        msg = (f"Le site utilise des outils de suivi ({', '.join(traceurs)}) "
               "mais aucun bandeau de consentement aux cookies n'a été détecté.")
    elif traceurs:
        st = OK
        msg = f"Outils de suivi détectés ({', '.join(traceurs)}) avec un gestionnaire de consentement ({', '.join(cmp)})."
    elif cmp:
        st = OK
        msg = f"Gestionnaire de consentement détecté ({', '.join(cmp)})."
    else:
        st = INFO
        msg = "Aucun outil de suivi ni bandeau cookies détecté dans le code de la page d'accueil."
    r.append(Resultat(
        "conformite.cookies", "conformite", "Consentement aux cookies", st, msg,
        "Installer un bandeau de consentement conforme (refuser aussi simple qu'accepter) "
        "et bloquer les outils de suivi tant que le visiteur n'a pas accepté.",
        3,
    ))

    return r


# --- Contrôles qui demandent d'autres requêtes -------------------------------


def robots_bloque_tout(texte: str) -> bool:
    """Vrai si le groupe `User-agent: *` (ou Googlebot) contient `Disallow: /`."""
    agents: list[str] = []
    dans_regles = False
    for ligne in texte.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if ":" not in ligne:
            continue
        cle, valeur = (p.strip() for p in ligne.split(":", 1))
        cle = cle.lower()
        if cle == "user-agent":
            if dans_regles:  # un nouveau groupe commence
                agents, dans_regles = [], False
            agents.append(valeur.lower())
        elif cle in ("disallow", "allow"):
            dans_regles = True
            if cle == "disallow" and valeur == "/" and ("*" in agents or "googlebot" in agents):
                return True
    return False


def controle_robots_sitemap(statut_robots: int | None, texte_robots: str,
                            statut_sitemap: int | None) -> list[Resultat]:
    r: list[Resultat] = []
    robots_ok = statut_robots == 200
    bloque_tout = robots_ok and robots_bloque_tout(texte_robots)
    if bloque_tout:
        st, msg = PROBLEME, "Le fichier robots.txt interdit à Google d'explorer tout le site."
    elif robots_ok:
        st, msg = OK, "Le fichier robots.txt est présent."
    else:
        st, msg = ATTENTION, "Le fichier robots.txt est absent."
    r.append(Resultat(
        "seo.robots", "seo", "Fichier robots.txt", st, msg,
        "Publier un robots.txt qui autorise l'exploration et indique l'adresse du sitemap.",
        3 if bloque_tout else 1,
    ))
    sitemap_declare = "sitemap:" in texte_robots.lower()
    ok = statut_sitemap == 200 or sitemap_declare
    r.append(Resultat(
        "seo.sitemap", "seo", "Plan du site pour Google (sitemap)",
        OK if ok else ATTENTION,
        "Un sitemap est disponible." if ok else "Aucun sitemap n'a été trouvé.",
        "Générer un sitemap.xml (automatique avec Yoast ou Rank Math) et le déclarer dans Google Search Console.",
        2,
    ))
    return r


def controle_liens(statuts: dict[str, int | None]) -> Resultat:
    casses = [u for u, s in statuts.items() if s is None or s >= 400]
    if not statuts:
        return Resultat("seo.liens", "seo", "Liens cassés", INFO,
                        "Aucun lien interne à vérifier sur la page d'accueil.",
                        "Vérifier que le menu pointe bien vers vos pages.", 2)
    return Resultat(
        "seo.liens", "seo", "Liens cassés",
        OK if not casses else PROBLEME,
        f"Les {len(statuts)} liens internes vérifiés fonctionnent." if not casses else
        f"{len(casses)} liens sur {len(statuts)} vérifiés mènent à une erreur, par exemple {casses[0]}.",
        "Corriger ou supprimer les liens qui mènent à une page d'erreur.",
        2,
    )


def controle_redirection_https(url_demandee: str, url_finale: str) -> Resultat | None:
    if not url_demandee.startswith("http://"):
        return None
    ok = url_finale.startswith("https://")
    return Resultat(
        "securite.redirection", "securite", "Redirection vers HTTPS",
        OK if ok else PROBLEME,
        "La version http redirige vers https." if ok else "La version http ne redirige pas vers https.",
        "Rediriger automatiquement toutes les adresses http vers https.",
        2,
    )
