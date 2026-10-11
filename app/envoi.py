"""Envoi du rapport par email via l'API transactionnelle Brevo (optionnel)."""

from __future__ import annotations

import html
import logging
import os

import httpx

log = logging.getLogger(__name__)
API_BREVO = "https://api.brevo.com/v3/smtp/email"


def email_active() -> bool:
    return bool(os.getenv("BREVO_API_KEY") and os.getenv("EXPEDITEUR_EMAIL"))


def contenu_email(url_site: str, score: int, lien_rapport: str, resume: str) -> str:
    e = html.escape
    return f"""<!doctype html><html lang="fr"><body style="margin:0;background:#f4f1ea;font-family:Arial,sans-serif;color:#1d2a26">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px">
<tr><td style="padding:32px">
<p style="margin:0 0 8px;font-size:14px;color:#5b6b66">Audit de {e(url_site)}</p>
<h1 style="margin:0 0 16px;font-size:26px">Votre site obtient {score}/100</h1>
<p style="margin:0 0 24px;font-size:16px;line-height:1.5">{e(resume)}</p>
<table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="background:#1f5c4a;border-radius:8px">
<a href="{e(lien_rapport)}" style="display:inline-block;padding:14px 22px;color:#ffffff;text-decoration:none;font-weight:bold">Voir le rapport complet</a>
</td></tr></table>
<p style="margin:24px 0 0;font-size:14px;line-height:1.5;color:#5b6b66">Une question sur un point du rapport ? Répondez simplement à cet email.</p>
</td></tr></table></td></tr></table></body></html>"""


def envoyer_rapport(destinataire: str, url_site: str, score: int, lien_rapport: str, resume: str) -> bool:
    if not email_active():
        return False
    try:
        resp = httpx.post(
            API_BREVO,
            headers={"api-key": os.environ["BREVO_API_KEY"], "accept": "application/json"},
            json={
                "sender": {"email": os.environ["EXPEDITEUR_EMAIL"],
                           "name": os.getenv("EXPEDITEUR_NOM", "Audit de site")},
                "to": [{"email": destinataire}],
                "subject": f"Votre audit de site : {score}/100",
                "htmlContent": contenu_email(url_site, score, lien_rapport, resume),
                "tags": ["audit-site"],
            },
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        log.exception("Envoi Brevo impossible")
        return False
