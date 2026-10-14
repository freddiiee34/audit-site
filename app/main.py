"""Application web : formulaire, lancement de l'audit, page de rapport."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # avant les imports qui lisent l'environnement

from fastapi import BackgroundTasks, FastAPI, Form, Request  # noqa: E402
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import envoi, stockage
from .analyse import AuditImpossible, auditer
from .checks import CATEGORIES
from .fetcher import URLRefusee
from .rapport import Rapport, rediger

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("audit")

ICI = Path(__file__).parent


@asynccontextmanager
async def cycle_de_vie(_app: FastAPI):
    stockage.initialiser()
    yield


app = FastAPI(title="Audit de site", docs_url=None, redoc_url=None, lifespan=cycle_de_vie)
app.mount("/static", StaticFiles(directory=ICI / "static"), name="static")
templates = Jinja2Templates(directory=ICI / "templates")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LIBELLES = {"ok": "Bon", "attention": "À améliorer", "probleme": "Problème", "info": "Info"}
# Vocabulaire du contrôle technique, pour le thème « alt »
LIBELLES_ALT = {"ok": "Conforme", "attention": "À reprendre", "probleme": "Défaillance", "info": "Pour info"}


def _photo() -> str:
    # Dépose simplement ta photo dans app/static/ sous l'un de ces noms.
    for nom in ("simon.webp", "simon.jpg", "simon.png"):
        if (ICI / "static" / nom).exists():
            return f"/static/{nom}"
    return ""


def _avis() -> list[dict]:
    # Uniquement de vrais avis clients, ajoutés à la main dans app/avis.json.
    try:
        avis = json.loads((ICI / "avis.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [a for a in avis if isinstance(a, dict) and a.get("texte") and a.get("auteur")][:3]


def _https(variable: str) -> str:
    valeur = os.getenv(variable, "")
    return valeur if valeur.startswith("https://") else ""


def contexte_commun() -> dict:
    return {
        "nom_service": os.getenv("NOM_SERVICE", "Audit de site"),
        "signature": os.getenv("SIGNATURE", "Simon Delrue, développeur web & IA"),
        "titre_pro": os.getenv("TITRE_PRO", "Développeur web & IA pour les PME"),
        "lien_realisations": _https("LIEN_REALISATIONS") or "/realisations",
        "lien_linkedin": _https("LIEN_LINKEDIN"),
        "photo": _photo(),
        "offre_prix": os.getenv("OFFRE_PRIX", "").strip(),
        "url_publique": (os.getenv("URL_PUBLIQUE") or "").rstrip("/"),
        # Thème par défaut : « contrôle technique » (style-alt.css). THEME=classique : première version.
        "theme": "" if os.getenv("THEME", "").strip().lower() == "classique" else "alt",
        # Lien facultatif vers un agenda en ligne (Calendly, Cal.com), proposé après la demande de RDV
        "lien_agenda": _https("LIEN_AGENDA"),
    }


SEUIL_COMPTEUR = 20  # en dessous, le compteur ferait plus de tort que de bien


def lien_absolu(request: Request, chemin: str) -> str:
    base = os.getenv("URL_PUBLIQUE") or str(request.base_url)
    return base.rstrip("/") + chemin


IDENT_RE = re.compile(r"[0-9a-f]{32}")


def ip_client(request: Request) -> str:
    if os.getenv("DERRIERE_PROXY") == "1":
        # Le proxy ajoute l'IP réelle EN DERNIER : les entrées précédentes
        # peuvent être inventées par le visiteur pour contourner les limites.
        transmis = request.headers.get("x-forwarded-for", "")
        if transmis:
            return transmis.split(",")[-1].strip()
    return request.client.host if request.client else "inconnue"


@app.get("/", response_class=HTMLResponse)
def accueil(request: Request):
    return templates.TemplateResponse(request, "accueil.html", _contexte_accueil())


def _contexte_accueil(**extra) -> dict:
    nb = stockage.nombre_audits()
    return contexte_commun() | {
        "nb_audits": nb if nb >= SEUIL_COMPTEUR else 0,
        "avis": _avis(),
    } | extra


@app.get("/sante")
def sante():
    return {"ok": True}


@app.post("/audit")
async def lancer_audit(
    request: Request,
    url: str = Form(""),
    site_web: str = Form(""),  # champ piège, invisible pour un humain
):
    def erreur(message: str, code: int = 400):
        ctx = _contexte_accueil(erreur=message, url=url)
        return templates.TemplateResponse(request, "accueil.html", ctx, status_code=code)

    if site_web:
        return erreur("Votre demande n'a pas pu être traitée.")
    if not url.strip():
        return erreur("Indiquez l'adresse de votre site, par exemple www.monentreprise.fr.")
    ip = ip_client(request)
    depasse = stockage.quota_ip(ip)
    if depasse:
        return erreur(depasse, 429)

    try:
        audit = await auditer(url)
    except (URLRefusee, AuditImpossible) as exc:
        return erreur(str(exc), 422)
    except Exception:
        log.exception("Échec inattendu de l'audit pour %s", url)
        return erreur("L'analyse a échoué de notre côté. Réessayez dans quelques minutes.", 500)

    rapport = await asyncio.to_thread(rediger, audit)
    # Pas encore d'email : le score est visible tout de suite, le détail se débloque ensuite.
    ident = stockage.enregistrer(audit.url, "", ip, audit.dict(), rapport.dict())
    log.info("Audit %s : %s -> %s/100 (IA=%s)", ident, audit.url_finale, audit.score, rapport.redige_par_ia)
    return RedirectResponse(f"/rapport/{ident}", status_code=303)


@app.post("/rapport/{ident}/email")
def debloquer_rapport(
    request: Request,
    ident: str,
    taches: BackgroundTasks,
    email: str = Form(""),
    consentement: str = Form(""),
    site_web: str = Form(""),  # champ piège
):
    donnees = _charger_audit(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    if donnees["email"]:
        return RedirectResponse(f"/rapport/{ident}#priorites", status_code=303)
    email = email.strip().lower()[:200]

    def erreur(message: str, code: int = 400):
        return _page_rapport(request, ident, donnees, erreur=message, email_saisi=email, code=code)

    if site_web:
        return erreur("Votre demande n'a pas pu être traitée.")
    if not EMAIL_RE.match(email):
        return erreur("Merci d'indiquer une adresse email valide.")
    if not consentement:
        return erreur("Merci d'accepter que j'utilise votre email pour vous envoyer le rapport.")
    depasse = stockage.quota_email(email)
    if depasse:
        return erreur(depasse, 429)

    if stockage.associer_email(ident, email):
        audit, rapport = donnees["audit"], donnees["rapport"]
        lien = lien_absolu(request, f"/rapport/{ident}")
        taches.add_task(_emails_audit, ident, email, audit["url_finale"], audit["score"], lien, rapport["resume"])
    return RedirectResponse(f"/rapport/{ident}#priorites", status_code=303)


def _emails_audit(ident: str, email: str, url: str, score: int, lien: str, resume: str) -> None:
    if envoi.envoyer_rapport(email, url, score, lien, resume):
        stockage.marquer_email_envoye(ident)
    envoi.notifier_nouvel_audit(email, url, score, lien)


# --- Pages légales, robots.txt, sitemap -----------------------------------------

def _editeur() -> dict:
    return {
        "nom": os.getenv("EDITEUR_NOM", "Simon Delrue"),
        "statut": os.getenv("EDITEUR_STATUT", "Entrepreneur individuel"),
        "siret": os.getenv("EDITEUR_SIRET", "984 352 666 00015"),
        "adresse": os.getenv("EDITEUR_ADRESSE", "3B rue Marceau, 59290 Wasquehal"),
        "email": os.getenv("CONTACT_EMAIL") or os.getenv("NOTIF_EMAIL", ""),
        "telephone": os.getenv("CONTACT_TELEPHONE", ""),
    }


@app.get("/mentions-legales", response_class=HTMLResponse)
def mentions_legales(request: Request):
    return templates.TemplateResponse(request, "mentions.html", contexte_commun() | {"editeur": _editeur()})


@app.get("/confidentialite", response_class=HTMLResponse)
def confidentialite(request: Request):
    return templates.TemplateResponse(request, "confidentialite.html", contexte_commun() | {"editeur": _editeur()})


REALISATIONS = [
    {
        "cle": "happy-hours",
        "type": "Landing page",
        "titre": "Happy Hours, pour Parions Sport en ligne (FDJ)",
        "contexte": "Une page pour annoncer une offre limitée dans le temps : chaque jour de 18 h à 20 h, les paris combinés sont boostés de 5 % supplémentaires.",
        "fait": [
            "Intégration fidèle de la maquette, en responsive",
            "Sélecteur de boost animé, que l'on fait glisser au doigt sur mobile",
            "Barre de navigation qui se fixe au défilement",
            "Mentions obligatoires des jeux d'argent intégrées",
        ],
        "outils": ["HTML", "LESS", "jQuery", "Polices personnalisées"],
    },
    {
        "cle": "emailing-sante",
        "type": "Emailing",
        "titre": "Complémentaire santé des intermittents, pour Audiens",
        "contexte": "Un emailing d'acquisition qui présente la complémentaire santé pensée pour les artistes et techniciens intermittents du spectacle, avec un appel à l'action « On vous rappelle ? ».",
        "fait": [
            "Code écrit à la main, compatible avec tous les logiciels de messagerie, Outlook compris",
            "Mise en page en tableaux et styles en ligne, la seule fiable en emailing",
            "Version mobile adaptée",
            "Mentions légales et liens vers les réseaux sociaux",
        ],
        "outils": ["HTML email", "CSS en ligne", "Responsive"],
    },
    {
        "cle": "galerie-ia",
        "type": "Landing page",
        "titre": "Galerie InnoDays 2023, pour TotalEnergies Marketing & Services",
        "contexte": "Une page événementielle qui présente les créations d'un concours d'images générées par IA, sur le thème « Unleashing the power of innovation with AI ».",
        "fait": [
            "Galerie d'une trentaine de visuels en mosaïque",
            "Chaque image s'ouvre en grand au clic",
            "Médaille or, argent ou bronze affichée pour le podium",
            "Affichage adapté du grand écran au téléphone",
        ],
        "outils": ["HTML", "LESS", "JavaScript"],
    },
]


@app.get("/realisations", response_class=HTMLResponse)
def realisations(request: Request):
    return templates.TemplateResponse(request, "realisations.html", contexte_commun() | {"projets": REALISATIONS})


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots(request: Request):
    return ("User-agent: *\nDisallow: /rapport/\nDisallow: /rendez-vous/\n\n"
            f"Sitemap: {lien_absolu(request, '/sitemap.xml')}\n")


@app.get("/sitemap.xml")
def sitemap(request: Request):
    urls = "".join(f"<url><loc>{lien_absolu(request, c)}</loc></url>"
                   for c in ("/", "/realisations", "/mentions-legales", "/confidentialite"))
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return Response(xml, media_type="application/xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/logo.svg", status_code=301)


# --- Prise de rendez-vous ----------------------------------------------------

def _charger_audit(ident: str) -> dict | None:
    return stockage.charger(ident) if IDENT_RE.fullmatch(ident) else None


@app.get("/rendez-vous/{ident}", response_class=HTMLResponse)
def formulaire_rdv(request: Request, ident: str, offre: str = ""):
    donnees = _charger_audit(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    champs = {"email": donnees["email"]}
    if offre == "correction":
        champs["message"] = "Je souhaite un devis pour corriger les points de mon rapport."
    ctx = contexte_commun() | {"ident": ident, "audit": donnees["audit"], "champs": champs,
                               "offre": offre == "correction"}
    return templates.TemplateResponse(request, "rdv.html", ctx)


@app.post("/rendez-vous/{ident}", response_class=HTMLResponse)
def demander_rdv(
    request: Request,
    ident: str,
    taches: BackgroundTasks,
    nom: str = Form(""),
    entreprise: str = Form(""),
    email: str = Form(""),
    telephone: str = Form(""),
    disponibilites: str = Form(""),
    message: str = Form(""),
    site_web: str = Form(""),  # champ piège
    offre: str = "",
):
    donnees = _charger_audit(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    audit = donnees["audit"]
    rdv = {
        "nom": nom.strip()[:100],
        "entreprise": entreprise.strip()[:150],
        "email": email.strip().lower()[:200],
        "telephone": telephone.strip()[:30],
        "disponibilites": disponibilites.strip()[:500],
        "message": message.strip()[:2000],
    }

    def erreur(texte: str, code: int = 400):
        ctx = contexte_commun() | {"ident": ident, "audit": audit, "champs": rdv, "erreur": texte,
                                   "offre": offre == "correction"}
        return templates.TemplateResponse(request, "rdv.html", ctx, status_code=code)

    if site_web:
        return erreur("Votre demande n'a pas pu être traitée.")
    if not rdv["nom"]:
        return erreur("Merci d'indiquer votre nom.")
    if not EMAIL_RE.match(rdv["email"]):
        return erreur("Merci d'indiquer une adresse email valide.")
    if rdv["telephone"] and not re.fullmatch(r"[0-9+().\s-]{6,30}", rdv["telephone"]):
        return erreur("Ce numéro de téléphone ne semble pas valide.")
    if stockage.nombre_rdv(ident) >= stockage.LIMITE_RDV_PAR_AUDIT:
        return erreur("Votre demande a déjà été transmise. Je reviens vers vous très vite.", 429)

    rdv_id = stockage.enregistrer_rdv(ident, rdv)
    log.info("Demande de RDV %s pour l'audit %s", rdv_id, ident)
    taches.add_task(_emails_rdv, rdv_id, rdv, audit["url_finale"], audit["score"],
                    lien_absolu(request, f"/rapport/{ident}"), contexte_commun()["signature"])

    ctx = contexte_commun() | {"ident": ident, "audit": audit, "envoye": True, "champs": rdv,
                               "offre": offre == "correction", "email_actif": envoi.email_active()}
    return templates.TemplateResponse(request, "rdv.html", ctx)


def _emails_rdv(rdv_id: int, rdv: dict, url: str, score: int, lien: str, signature: str) -> None:
    if envoi.notifier_rdv(rdv, url, score, lien):
        stockage.marquer_rdv_notifie(rdv_id)
    envoi.confirmer_rdv(rdv, signature)


@app.get("/rapport/{ident}", response_class=HTMLResponse)
def voir_rapport(request: Request, ident: str):
    donnees = _charger_audit(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    return _page_rapport(request, ident, donnees)


def _page_rapport(request: Request, ident: str, donnees: dict, code: int = 200, **extra):
    audit = donnees["audit"]
    rapport = Rapport.depuis_dict(donnees["rapport"])
    par_categorie: dict[str, list[dict]] = {}
    for r in audit["resultats"]:
        par_categorie.setdefault(r["categorie"], []).append(r)
    ctx = contexte_commun() | {
        "ident": ident,
        "audit": audit,
        "rapport": rapport,
        "debloque": bool(donnees["email"]),
        "categories": CATEGORIES,
        "par_categorie": par_categorie,
        "libelles": LIBELLES_ALT if contexte_commun()["theme"] == "alt" else LIBELLES,
        "email_envoye": envoi.email_active(),
    } | extra
    return templates.TemplateResponse(request, "rapport.html", ctx, status_code=code)
