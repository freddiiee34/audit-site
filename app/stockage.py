"""Stockage SQLite : audits réalisés, leads et limites d'utilisation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

LIMITE_PAR_EMAIL = int(os.getenv("AUDIT_LIMITE_EMAIL", "3"))
LIMITE_PAR_IP = int(os.getenv("AUDIT_LIMITE_IP", "10"))
FENETRE = timedelta(hours=24)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audits (
    id TEXT PRIMARY KEY,
    cree_le TEXT NOT NULL,
    url TEXT NOT NULL,
    email TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    score INTEGER NOT NULL,
    audit_json TEXT NOT NULL,
    rapport_json TEXT NOT NULL,
    email_envoye INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audits_email ON audits(email, cree_le);
CREATE TABLE IF NOT EXISTS rendez_vous (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL REFERENCES audits(id),
    cree_le TEXT NOT NULL,
    nom TEXT NOT NULL,
    entreprise TEXT NOT NULL,
    email TEXT NOT NULL,
    telephone TEXT NOT NULL,
    disponibilites TEXT NOT NULL,
    message TEXT NOT NULL,
    notifie INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rdv_audit ON rendez_vous(audit_id);
CREATE INDEX IF NOT EXISTS idx_audits_ip ON audits(ip_hash, cree_le);
"""


def chemin_base() -> str:
    return os.getenv("AUDIT_DB", "audits.db")


@contextmanager
def connexion():
    con = sqlite3.connect(chemin_base())
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def initialiser() -> None:
    with connexion() as con:
        con.executescript(SCHEMA)


def hacher_ip(ip: str) -> str:
    # On ne garde pas l'IP en clair : un hachage salé suffit pour limiter les abus.
    sel = os.getenv("AUDIT_SEL", "change-moi")
    return hashlib.sha256((sel + ip).encode()).hexdigest()[:32]


def quota_ip(ip: str) -> str | None:
    """Limite le nombre d'analyses lancées depuis une même connexion."""
    depuis = (datetime.now(timezone.utc) - FENETRE).isoformat()
    with connexion() as con:
        nb = con.execute("SELECT COUNT(*) FROM audits WHERE ip_hash = ? AND cree_le > ?",
                         (hacher_ip(ip), depuis)).fetchone()[0]
    if nb >= LIMITE_PAR_IP:
        return "Trop d'analyses ont été lancées depuis votre connexion aujourd'hui. Réessayez demain."
    return None


def quota_email(email: str) -> str | None:
    """Limite le nombre de rapports envoyés à une même adresse."""
    depuis = (datetime.now(timezone.utc) - FENETRE).isoformat()
    with connexion() as con:
        nb = con.execute("SELECT COUNT(*) FROM audits WHERE email = ? AND cree_le > ?",
                         (email, depuis)).fetchone()[0]
    if nb >= LIMITE_PAR_EMAIL:
        return f"Vous avez déjà reçu {nb} rapports aujourd'hui. Revenez demain ou écrivez-moi directement."
    return None


def associer_email(ident: str, email: str) -> bool:
    """Débloque le rapport complet. Ne remplace jamais un email déjà enregistré."""
    with connexion() as con:
        cur = con.execute("UPDATE audits SET email = ? WHERE id = ? AND email = ''", (email, ident))
        return cur.rowcount == 1


def nombre_audits() -> int:
    with connexion() as con:
        return con.execute("SELECT COUNT(*) FROM audits").fetchone()[0]


def enregistrer(url: str, email: str, ip: str, audit: dict, rapport: dict) -> str:
    ident = uuid.uuid4().hex
    with connexion() as con:
        con.execute(
            "INSERT INTO audits (id, cree_le, url, email, ip_hash, score, audit_json, rapport_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ident, datetime.now(timezone.utc).isoformat(), url, email, hacher_ip(ip), audit["score"],
             json.dumps(audit, ensure_ascii=False), json.dumps(rapport, ensure_ascii=False)),
        )
    return ident


def marquer_email_envoye(ident: str) -> None:
    with connexion() as con:
        con.execute("UPDATE audits SET email_envoye = 1 WHERE id = ?", (ident,))


LIMITE_RDV_PAR_AUDIT = 3


def nombre_rdv(audit_id: str) -> int:
    with connexion() as con:
        return con.execute("SELECT COUNT(*) FROM rendez_vous WHERE audit_id = ?", (audit_id,)).fetchone()[0]


def enregistrer_rdv(audit_id: str, rdv: dict) -> int:
    with connexion() as con:
        cur = con.execute(
            "INSERT INTO rendez_vous (audit_id, cree_le, nom, entreprise, email, telephone, disponibilites, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (audit_id, datetime.now(timezone.utc).isoformat(), rdv["nom"], rdv["entreprise"], rdv["email"],
             rdv["telephone"], rdv["disponibilites"], rdv["message"]),
        )
        return cur.lastrowid


def marquer_rdv_notifie(rdv_id: int) -> None:
    with connexion() as con:
        con.execute("UPDATE rendez_vous SET notifie = 1 WHERE id = ?", (rdv_id,))


def charger(ident: str) -> dict | None:
    with connexion() as con:
        ligne = con.execute("SELECT * FROM audits WHERE id = ?", (ident,)).fetchone()
    if not ligne:
        return None
    return {
        "id": ligne["id"],
        "cree_le": ligne["cree_le"],
        "email": ligne["email"],
        "audit": json.loads(ligne["audit_json"]),
        "rapport": json.loads(ligne["rapport_json"]),
    }
