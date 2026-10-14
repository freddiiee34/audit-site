"""Envoi des emails via l'API transactionnelle Brevo.

Trois emails :
- au visiteur : son rapport d'audit ;
- à toi (NOTIF_EMAIL) : une alerte à chaque nouvel audit, puis à chaque demande
  de rendez-vous, avec toutes les infos du prospect ;
- au visiteur : la confirmation de sa demande de rendez-vous.

Sans BREVO_API_KEY ni EXPEDITEUR_EMAIL, rien n'est envoyé et l'application
fonctionne quand même (les demandes restent enregistrées dans la base).
"""

from __future__ import annotations

import html
import logging
import os

import httpx

log = logging.getLogger(__name__)
API_BREVO = "https://api.brevo.com/v3/smtp/email"
e = html.escape


def email_active() -> bool:
    return bool(os.getenv("BREVO_API_KEY") and os.getenv("EXPEDITEUR_EMAIL"))


def email_notification() -> str | None:
    return os.getenv("NOTIF_EMAIL") or None


def envoyer(destinataire: str, sujet: str, contenu_html: str, repondre_a: str | None = None,
            tag: str = "audit-site") -> bool:
    if not email_active():
        log.warning("Email non envoyé (%s) : BREVO_API_KEY ou EXPEDITEUR_EMAIL manquant", sujet)
        return False
    corps = {
        "sender": {"email": os.environ["EXPEDITEUR_EMAIL"], "name": os.getenv("EXPEDITEUR_NOM", "Audit de site")},
        "to": [{"email": destinataire}],
        "subject": sujet,
        "htmlContent": contenu_html,
        "tags": [tag],
    }
    if repondre_a:
        corps["replyTo"] = {"email": repondre_a}
    try:
        resp = httpx.post(API_BREVO, json=corps, timeout=15,
                          headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"})
        if resp.status_code >= 400:
            # Le message de Brevo dit précisément ce qui coince (expéditeur non validé, IP non autorisée…)
            log.error("Brevo a refusé l'email « %s » (%s) : %s", sujet, resp.status_code, resp.text[:500])
            return False
        return True
    except httpx.HTTPError:
        log.exception("Envoi Brevo impossible (« %s »)", sujet)
        return False


# --- Mise en page commune ----------------------------------------------------

def _gabarit(contenu: str) -> str:
    return f"""<!doctype html><html lang="fr"><body style="margin:0;background:#f5f5f7;font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1d1d1f">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:14px">
<tr><td style="padding:32px">{contenu}</td></tr></table></td></tr></table></body></html>"""


def _bouton(lien: str, texte: str) -> str:
    return (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#0071e3;border-radius:980px">'
            f'<a href="{e(lien)}" style="display:inline-block;padding:13px 24px;color:#ffffff;text-decoration:none;font-weight:600">{e(texte)}</a>'
            f'</td></tr></table>')


def _tableau(lignes: list[tuple[str, str]]) -> str:
    cellules = "".join(
        f'<tr><td style="padding:8px 12px 8px 0;color:#6e6e73;vertical-align:top;white-space:nowrap">{e(k)}</td>'
        f'<td style="padding:8px 0;white-space:pre-wrap">{e(v) if v else "—"}</td></tr>'
        for k, v in lignes
    )
    return f'<table role="presentation" cellpadding="0" cellspacing="0" style="font-size:15px;line-height:1.45;margin:0 0 24px">{cellules}</table>'


# --- Emails ------------------------------------------------------------------

def envoyer_rapport(destinataire: str, url_site: str, score: int, lien_rapport: str, resume: str) -> bool:
    contenu = f"""<p style="margin:0 0 8px;font-size:14px;color:#6e6e73">Audit de {e(url_site)}</p>
<h1 style="margin:0 0 16px;font-size:26px">Votre site obtient {score}/100</h1>
<p style="margin:0 0 24px;font-size:16px;line-height:1.5">{e(resume)}</p>
{_bouton(lien_rapport, "Voir le rapport complet")}
<p style="margin:24px 0 0;font-size:14px;line-height:1.5;color:#6e6e73">Une question sur un point du rapport ? Répondez simplement à cet email.</p>"""
    return envoyer(destinataire, f"Votre audit de site : {score}/100", _gabarit(contenu),
                   repondre_a=email_notification(), tag="audit-rapport")


def notifier_nouvel_audit(email_prospect: str, url_site: str, score: int, lien_rapport: str) -> bool:
    dest = email_notification()
    if not dest:
        return False
    contenu = f"""<h1 style="margin:0 0 16px;font-size:22px">Nouvel audit : {score}/100</h1>
{_tableau([("Site", url_site), ("Email", email_prospect), ("Score", f"{score}/100")])}
{_bouton(lien_rapport, "Voir son rapport")}"""
    return envoyer(dest, f"Nouvel audit : {url_site} ({score}/100)", _gabarit(contenu),
                   repondre_a=email_prospect, tag="audit-lead")


def notifier_rdv(rdv: dict, url_site: str, score: int, lien_rapport: str) -> bool:
    dest = email_notification()
    if not dest:
        return False
    contenu = f"""<h1 style="margin:0 0 6px;font-size:22px">Demande de rendez-vous</h1>
<p style="margin:0 0 20px;font-size:15px;color:#6e6e73">Répondre à cet email écrit directement au prospect.</p>
{_tableau([
    ("Nom", rdv["nom"]),
    ("Entreprise", rdv["entreprise"]),
    ("Email", rdv["email"]),
    ("Téléphone", rdv["telephone"]),
    ("Disponibilités", rdv["disponibilites"]),
    ("Message", rdv["message"]),
    ("Site audité", url_site),
    ("Score", f"{score}/100"),
])}
{_bouton(lien_rapport, "Voir son rapport")}"""
    return envoyer(dest, f"RDV demandé : {rdv['nom']} ({url_site})", _gabarit(contenu),
                   repondre_a=rdv["email"], tag="audit-rdv")


def confirmer_rdv(rdv: dict, signature: str) -> bool:
    contenu = f"""<h1 style="margin:0 0 16px;font-size:22px">Demande bien reçue</h1>
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Bonjour {e(rdv["nom"])},</p>
<p style="margin:0 0 16px;font-size:16px;line-height:1.5">Merci pour votre demande. Je reviens vers vous sous 24 h ouvrées pour fixer un créneau de 20 minutes, selon les disponibilités que vous m'avez indiquées.</p>
<p style="margin:0;font-size:16px;line-height:1.5">{e(signature)}</p>"""
    return envoyer(rdv["email"], "Votre demande de rendez-vous", _gabarit(contenu),
                   repondre_a=email_notification(), tag="audit-rdv-confirmation")
