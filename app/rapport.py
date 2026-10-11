"""Rédaction du rapport pour un gérant de PME.

L'IA reçoit uniquement les constats mesurés et les reformule en langage simple.
Garde-fous :
- elle doit renvoyer du JSON qui cite les identifiants des contrôles ;
- toute priorité qui ne correspond à aucun contrôle en échec est écartée ;
- sans clé API, ou si la réponse est inexploitable, on bascule sur un rapport
  rédigé à partir des conseils intégrés aux contrôles. Le service ne tombe jamais.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from .analyse import Audit
from .checks import CATEGORIES, ENJEUX, PROBLEME

log = logging.getLogger(__name__)

MODELE_PAR_DEFAUT = "claude-haiku-5-5"

CONSIGNES = """Tu es consultant web. Tu rédiges le rapport d'audit d'un site pour son gérant, \
une personne qui n'est pas technicienne (artisan, commerçant, dirigeant de PME).

Règles strictes :
- Utilise UNIQUEMENT les constats fournis. N'invente aucun chiffre, aucun problème, aucun outil.
- Français simple, phrases courtes, vouvoiement, aucun jargon sans explication.
- Explique l'enjeu concret pour l'entreprise : clients perdus, visibilité Google, risque légal, image.
- Ton factuel et encourageant, jamais alarmiste ni commercial.

Réponds uniquement avec un objet JSON, sans texte autour :
{
  "resume": "2 à 3 phrases : état général du site et ce qui compte le plus",
  "priorites": [
    {"id": "<identifiant exact d'un contrôle fourni>",
     "titre": "titre court et parlant",
     "pourquoi": "1 à 2 phrases : ce que ça coûte à l'entreprise",
     "comment": "1 à 2 phrases : quoi faire concrètement"}
  ],
  "points_forts": ["1 à 3 points positifs, tirés des contrôles réussis"]
}
Garde l'ordre des priorités fourni. Maximum 5 priorités."""


@dataclass
class Priorite:
    id: str
    titre: str
    pourquoi: str
    comment: str
    statut: str


@dataclass
class Rapport:
    resume: str
    priorites: list[Priorite]
    points_forts: list[str]
    redige_par_ia: bool

    def dict(self) -> dict:
        return {
            "resume": self.resume,
            "priorites": [p.__dict__ for p in self.priorites],
            "points_forts": self.points_forts,
            "redige_par_ia": self.redige_par_ia,
        }

    @classmethod
    def depuis_dict(cls, d: dict) -> "Rapport":
        return cls(d["resume"], [Priorite(**p) for p in d["priorites"]], d["points_forts"], d["redige_par_ia"])


def _niveau(score: int) -> str:
    if score >= 80:
        return "en bonne santé"
    if score >= 55:
        return "correct, avec des points importants à corriger"
    return "pénalisé par plusieurs problèmes importants"


def rapport_sans_ia(audit: Audit) -> Rapport:
    nb_pb = sum(1 for r in audit.resultats if r.statut == PROBLEME)
    resume = f"Votre site obtient {audit.score}/100 : il est {_niveau(audit.score)}. "
    if nb_pb > len(audit.priorites):
        resume += f"Nous avons relevé {nb_pb} problèmes ; voici les {len(audit.priorites)} plus urgents."
    elif nb_pb:
        resume += f"Nous avons relevé {nb_pb} problème{'s' if nb_pb > 1 else ''} à traiter en priorité."
    else:
        resume += "Aucun problème bloquant n'a été relevé ; quelques améliorations restent possibles."
    priorites = [_priorite_standard(r) for r in audit.priorites]
    forts = [r.constat for r in audit.resultats if r.statut == "ok" and r.impact == 3][:3]
    return Rapport(resume, priorites, forts, redige_par_ia=False)


def _priorite_standard(r) -> Priorite:
    pourquoi = f"{r.constat} {ENJEUX[r.id]}" if r.id in ENJEUX else r.constat
    return Priorite(r.id, r.titre, pourquoi, r.conseil, r.statut)


def _donnees_pour_ia(audit: Audit) -> str:
    return json.dumps({
        "site": audit.url_finale,
        "score_global_sur_100": audit.score,
        "scores_par_categorie": {CATEGORIES[c]: s for c, s in audit.scores_categories.items()},
        "priorites_a_traiter_dans_cet_ordre": [
            {"id": r.id, "titre": r.titre, "statut": r.statut, "constat": r.constat,
             "enjeu": ENJEUX.get(r.id, ""), "conseil": r.conseil}
            for r in audit.priorites
        ],
        "controles_reussis": [r.constat for r in audit.resultats if r.statut == "ok"],
    }, ensure_ascii=False, indent=1)


def _extraire_json(texte: str) -> dict:
    texte = texte.strip()
    texte = re.sub(r"^```(?:json)?\s*|\s*```$", "", texte)
    debut, fin = texte.find("{"), texte.rfind("}")
    return json.loads(texte[debut:fin + 1])


def valider(brut: dict, audit: Audit) -> Rapport:
    """Ne garde que ce qui est rattaché à un constat réel."""
    statut_par_id = {r.id: r.statut for r in audit.priorites}
    priorites, vus = [], set()
    for p in brut.get("priorites", [])[:5]:
        pid = p.get("id")
        if pid in statut_par_id and pid not in vus and all(isinstance(p.get(k), str) and p[k].strip()
                                                           for k in ("titre", "pourquoi", "comment")):
            priorites.append(Priorite(pid, p["titre"].strip(), p["pourquoi"].strip(), p["comment"].strip(),
                                      statut_par_id[pid]))
            vus.add(pid)
    resume = brut.get("resume")
    if not isinstance(resume, str) or not resume.strip():
        raise ValueError("résumé absent")
    if audit.priorites and not priorites:
        raise ValueError("aucune priorité valide")
    # Les priorités oubliées par l'IA sont complétées avec la version sans IA.
    for r in audit.priorites:
        if r.id not in vus:
            priorites.append(_priorite_standard(r))
    ordre = [r.id for r in audit.priorites]
    priorites.sort(key=lambda p: ordre.index(p.id))
    forts = [f.strip() for f in brut.get("points_forts", []) if isinstance(f, str) and f.strip()][:3]
    return Rapport(resume.strip(), priorites, forts, redige_par_ia=True)


def rediger(audit: Audit, client=None) -> Rapport:
    cle = os.getenv("ANTHROPIC_API_KEY")
    if client is None and not cle:
        return rapport_sans_ia(audit)
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(api_key=cle, timeout=45, max_retries=1)
        reponse = client.messages.create(
            model=os.getenv("AUDIT_MODELE", MODELE_PAR_DEFAUT),
            max_tokens=1500,
            system=CONSIGNES,
            messages=[{"role": "user", "content": "Constats de l'audit :\n" + _donnees_pour_ia(audit)}],
        )
        texte = "".join(b.text for b in reponse.content if getattr(b, "type", "") == "text")
        return valider(_extraire_json(texte), audit)
    except Exception:  # le rapport doit toujours sortir, même si l'IA échoue
        log.exception("Rédaction IA impossible, repli sur le rapport standard")
        return rapport_sans_ia(audit)
