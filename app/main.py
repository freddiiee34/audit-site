"""Application web : formulaire, lancement de l'audit, page de rapport."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # avant les imports qui lisent l'environnement

from fastapi import BackgroundTasks, FastAPI, Form, Request  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse
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


def contexte_commun() -> dict:
    agenda = os.getenv("LIEN_AGENDA", "")
    return {
        "nom_service": os.getenv("NOM_SERVICE", "Audit de site"),
        "signature": os.getenv("SIGNATURE", "Simon Delrue, développeur web"),
        # Lien facultatif vers un agenda en ligne (Calendly, Cal.com), proposé après la demande de RDV
        "lien_agenda": agenda if agenda.startswith("https://") else "",
    }


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
    return templates.TemplateResponse(request, "accueil.html", contexte_commun())


@app.get("/sante")
def sante():
    return {"ok": True}


@app.post("/audit")
async def lancer_audit(
    request: Request,
    taches: BackgroundTasks,
    url: str = Form(""),
    email: str = Form(""),
    consentement: str = Form(""),
    site_web: str = Form(""),  # champ piège, invisible pour un humain
):
    email = email.strip().lower()

    def erreur(message: str, code: int = 400):
        ctx = contexte_commun() | {"erreur": message, "url": url, "email": email}
        return templates.TemplateResponse(request, "accueil.html", ctx, status_code=code)

    if site_web:
        return erreur("Votre demande n'a pas pu être traitée.")
    if not EMAIL_RE.match(email):
        return erreur("Merci d'indiquer une adresse email valide pour recevoir votre rapport.")
    if not consentement:
        return erreur("Merci d'accepter que nous utilisions votre email pour vous envoyer le rapport.")
    ip = ip_client(request)
    depasse = stockage.quota_depasse(email, ip)
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
    ident = stockage.enregistrer(audit.url, email, ip, audit.dict(), rapport.dict())
    log.info("Audit %s : %s -> %s/100 (IA=%s)", ident, audit.url_finale, audit.score, rapport.redige_par_ia)

    lien = lien_absolu(request, f"/rapport/{ident}")
    taches.add_task(_emails_audit, ident, email, audit.url_finale, audit.score, lien, rapport.resume)

    return RedirectResponse(f"/rapport/{ident}", status_code=303)


def _emails_audit(ident: str, email: str, url: str, score: int, lien: str, resume: str) -> None:
    if envoi.envoyer_rapport(email, url, score, lien, resume):
        stockage.marquer_email_envoye(ident)
    envoi.notifier_nouvel_audit(email, url, score, lien)


# --- Prise de rendez-vous ----------------------------------------------------

def _charger_audit(ident: str) -> dict | None:
    return stockage.charger(ident) if IDENT_RE.fullmatch(ident) else None


@app.get("/rendez-vous/{ident}", response_class=HTMLResponse)
def formulaire_rdv(request: Request, ident: str):
    donnees = _charger_audit(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    ctx = contexte_commun() | {"ident": ident, "audit": donnees["audit"],
                               "champs": {"email": donnees["email"]}}
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
        ctx = contexte_commun() | {"ident": ident, "audit": audit, "champs": rdv, "erreur": texte}
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
                               "email_actif": envoi.email_active()}
    return templates.TemplateResponse(request, "rdv.html", ctx)


def _emails_rdv(rdv_id: int, rdv: dict, url: str, score: int, lien: str, signature: str) -> None:
    if envoi.notifier_rdv(rdv, url, score, lien):
        stockage.marquer_rdv_notifie(rdv_id)
    envoi.confirmer_rdv(rdv, signature)


@app.get("/rapport/{ident}", response_class=HTMLResponse)
def voir_rapport(request: Request, ident: str):
    if not IDENT_RE.fullmatch(ident):
        return HTMLResponse("Rapport introuvable.", status_code=404)
    donnees = stockage.charger(ident)
    if not donnees:
        return HTMLResponse("Rapport introuvable.", status_code=404)
    audit = donnees["audit"]
    rapport = Rapport.depuis_dict(donnees["rapport"])
    par_categorie: dict[str, list[dict]] = {}
    for r in audit["resultats"]:
        par_categorie.setdefault(r["categorie"], []).append(r)
    ctx = contexte_commun() | {
        "ident": ident,
        "audit": audit,
        "rapport": rapport,
        "categories": CATEGORIES,
        "par_categorie": par_categorie,
        "libelles": LIBELLES,
        "email_envoye": envoi.email_active(),
    }
    return templates.TemplateResponse(request, "rapport.html", ctx)

