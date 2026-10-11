# Audit de site

Un gérant de PME entre l'adresse de son site et reçoit en moins d'une minute un rapport clair, sans jargon, avec ses 5 priorités. En échange, il laisse son email : chaque rapport est un lead.

## Ce que l'outil vérifie

| Catégorie | Contrôles |
| --- | --- |
| Sécurité | HTTPS, redirection http → https, en-tête HSTS |
| Vitesse | Temps de réponse, poids du HTML, nombre de fichiers, chargement différé des images |
| Référencement Google | Titre, description, H1, noindex, canonical, langue, Open Graph, robots.txt, sitemap, liens internes cassés (20 max) |
| Mobile | Balise viewport |
| Obligations légales | Mentions légales, politique de confidentialité, outils de suivi sans bandeau de consentement |
| Accessibilité | Images sans `alt`, champs de formulaire sans étiquette |

Chaque contrôle produit un constat mesuré, un enjeu (ce que ça coûte à l'entreprise) et un conseil. Le score sur 100 est une moyenne pondérée par l'impact de chaque contrôle.

## Le rôle de l'IA, et ses garde-fous

L'IA ne fait **pas** l'analyse : elle reçoit uniquement les constats mesurés et les reformule pour un non-technicien.

- Elle doit répondre en JSON en citant l'identifiant de chaque contrôle. Une priorité qui ne correspond à aucun contrôle en échec est écartée : l'IA ne peut pas inventer de problème.
- L'ordre des priorités est fixé par le code, pas par l'IA.
- Sans clé API, ou si la réponse est inexploitable, le rapport est rédigé à partir des textes intégrés aux contrôles. Le service ne tombe jamais.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis remplir les valeurs
uvicorn app.main:app --reload
```

Puis ouvrir http://127.0.0.1:8000.

## Configuration (`.env`)

| Variable | Rôle |
| --- | --- |
| `ANTHROPIC_API_KEY` | Active la rédaction par l'IA (facultatif) |
| `AUDIT_MODELE` | Modèle utilisé (défaut : `claude-haiku-5-5`, le moins cher) |
| `BREVO_API_KEY`, `EXPEDITEUR_EMAIL`, `EXPEDITEUR_NOM` | Envoi du rapport par email (facultatif) |
| `LIEN_RDV` | Lien du bouton « Prendre rendez-vous » (Calendly, Cal.com, mailto…) |
| `NOM_SERVICE`, `SIGNATURE` | Nom affiché et signature en pied de page |
| `AUDIT_DB` | Chemin de la base SQLite (défaut : `audits.db`) |
| `AUDIT_SEL` | Sel pour hacher les adresses IP : **à changer** |
| `AUDIT_LIMITE_EMAIL`, `AUDIT_LIMITE_IP` | Audits autorisés par 24 h (défaut : 3 et 10) |
| `DERRIERE_PROXY` | `1` si l'app tourne derrière un proxy (lit `X-Forwarded-For`) |

## Sécurité et RGPD

- **Anti-SSRF** : les adresses internes (localhost, réseaux privés, métadonnées cloud) sont refusées, y compris après une redirection.
- **Anti-abus** : limite par email et par IP, champ piège invisible contre les robots.
- **RGPD** : case de consentement obligatoire, IP stockée hachée (jamais en clair), pages de rapport en `noindex`.
- Les rapports sont accessibles par un lien non devinable (identifiant aléatoire de 32 caractères).

## Tests

```bash
pip install pytest
python -m pytest
```

Les tests lancent deux faux sites en local (un bon, un mauvais) et vérifient chaque contrôle, le score, les priorités, le filtrage des réponses de l'IA, les replis, le blocage des adresses internes, les limites et le parcours web complet.

## Mise en ligne

Le plus simple : un petit VPS ou une plateforme comme Render, Railway ou Fly.io.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

Mets un reverse proxy (Caddy ou Nginx) devant pour le HTTPS, avec `DERRIERE_PROXY=1`. Monte le fichier SQLite sur un volume persistant, sinon les rapports disparaissent à chaque redéploiement.

## Limites connues (prochaines étapes)

- Seule la page d'accueil est analysée en profondeur.
- La vitesse est mesurée côté serveur, pas dans un navigateur. Brancher l'API PageSpeed Insights donnerait les Core Web Vitals.
- La détection des bandeaux cookies repose sur une liste d'outils connus : un bandeau fait maison peut ne pas être reconnu.
- Le contrôle anti-SSRF résout le domaine avant la connexion ; un DNS qui change entre-temps (rebinding) n'est pas couvert. Pour une protection complète, faire passer les requêtes sortantes par un proxy filtrant.
- L'audit est synchrone (20 à 40 s). Au-delà de quelques dizaines d'audits par jour, passer à une file de tâches.
