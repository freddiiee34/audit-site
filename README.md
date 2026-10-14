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

## Le parcours et les emails

1. Le visiteur entre l'adresse de son site, **sans email**. Pendant l'analyse, les étapes défilent à l'écran. Il voit tout de suite son **score**, les notes par catégorie et les titres de ses 5 priorités.
2. Pour le **rapport complet** (pourquoi chaque point compte, comment le corriger, détail des contrôles), il donne son email : il reçoit le rapport par email, et toi une **alerte « nouvel audit »** (site, email, score, lien du rapport).
3. Sous les priorités, deux offres : **« Je corrige ces points pour vous »** (devis, prix réglable avec `OFFRE_PRIX`) et **« On lit le rapport ensemble »** (appel de 20 minutes offert). Les deux ouvrent un formulaire (nom, entreprise, email, téléphone, disponibilités, message).
4. À l'envoi, tu reçois un email **« RDV demandé »** avec toutes ses infos et le lien de son rapport. Répondre à cet email écrit directement au prospect. Le visiteur reçoit une confirmation.

Toutes les demandes sont aussi enregistrées dans la base (table `rendez_vous`), même si l'envoi d'email échoue.

## Le rôle de l'IA, et ses garde-fous

L'IA ne fait **pas** l'analyse : elle reçoit uniquement les constats mesurés et les reformule pour un non-technicien.

- Elle doit répondre en JSON en citant l'identifiant de chaque contrôle. Une priorité qui ne correspond à aucun contrôle en échec est écartée : l'IA ne peut pas inventer de problème.
- L'ordre des priorités est fixé par le code, pas par l'IA.
- Sans clé API, ou si la réponse est inexploitable, le rapport est rédigé à partir des textes intégrés aux contrôles. Le service ne tombe jamais.

## Installation

Prérequis : Python 3.10 ou plus (`python3 --version` pour vérifier).

### Windows (invite de commandes)

```
cd chemin\vers\audit-site
python3 -m venv .venv
.venv\Scripts\activate
python3 -m pip install -r requirements.txt
copy .env.example .env
notepad .env
python3 -m uvicorn app.main:app --reload
```

Si `python3` n'est pas reconnu, remplace-le par `py` (par exemple `py -m venv .venv`).

### Mac et Linux (Terminal)

```bash
cd chemin/vers/audit-site
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
open -e .env        # sous Linux : nano .env
python3 -m uvicorn app.main:app --reload
```

### Ensuite

Ouvre http://127.0.0.1:8000 dans ton navigateur. Ctrl + C dans le terminal arrête le serveur.

Les fois suivantes, il suffit de te placer dans le dossier, d'activer l'environnement (`.venv\Scripts\activate` sous Windows, `source .venv/bin/activate` sur Mac) et de relancer la dernière commande.

**Pour tester en local**, passe `AUDIT_LIMITE_EMAIL` à 100 dans le `.env` : la limite par défaut (3 audits par email et par jour) te bloquerait vite.

### En cas de problème

| Message | Solution |
| --- | --- |
| `'source' n'est pas reconnu…` | Tu es sous Windows : utilise `.venv\Scripts\activate` |
| `python3` introuvable | Windows : utilise `py`. Mac : `xcode-select --install`, ou installe Python depuis python.org |
| `pip` ou `uvicorn` introuvable | Préfixe par `python3 -m` (`python3 -m pip …`, `python3 -m uvicorn …`) et vérifie que `(.venv)` apparaît au début de la ligne |
| Dossier introuvable après `cd` | Tape `cd ` puis glisse le dossier depuis l'Explorateur ou le Finder dans la fenêtre |
| « Vous avez déjà demandé 3 audits » | Augmente `AUDIT_LIMITE_EMAIL` dans le `.env` |

## Configuration (`.env`)

| Variable | Rôle |
| --- | --- |
| `ANTHROPIC_API_KEY` | Active la rédaction par l'IA (facultatif) |
| `AUDIT_MODELE` | Modèle utilisé (défaut : `claude-haiku-5-5`, le moins cher) |
| `BREVO_API_KEY`, `EXPEDITEUR_EMAIL`, `EXPEDITEUR_NOM` | Active l'envoi de tous les emails. Sans eux, rien n'est envoyé |
| `NOTIF_EMAIL` | Où tu reçois les alertes de nouvel audit et les demandes de RDV |
| `URL_PUBLIQUE` | Adresse du site utilisée dans les liens des emails (ex. `https://audit.simondelrue.fr`) |
| `LIEN_AGENDA` | Facultatif : lien Calendly ou Cal.com proposé après une demande de RDV |
| `NOM_SERVICE`, `SIGNATURE` | Nom du service et signature en pied de page |
| `THEME` | Vide : direction « contrôle technique » (`style-alt.css`). `classique` : première version (`style.css`) |
| `TITRE_PRO` | Ton titre, affiché dans l'en-tête et le bloc « Qui est derrière l'outil » (défaut : `Développeur web & IA pour les PME`) |
| `LIEN_REALISATIONS`, `LIEN_LINKEDIN` | Lien externe vers tes réalisations (défaut : la page `/realisations` du site) et ton LinkedIn (masqué si vide) |
| `OFFRE_PRIX` | Prix d'appel de l'offre de correction, ex. `290 €`. Vide : « Devis gratuit, sans engagement » |
| `EDITEUR_NOM`, `EDITEUR_STATUT`, `EDITEUR_SIRET`, `EDITEUR_ADRESSE`, `CONTACT_EMAIL`, `CONTACT_TELEPHONE` | Infos des mentions légales et de la politique de confidentialité. **SIRET et adresse sont obligatoires** pour un professionnel |
| `AUDIT_DB` | Chemin de la base SQLite (défaut : `audits.db`) |
| `AUDIT_SEL` | Sel pour hacher les adresses IP : **à changer** |
| `AUDIT_LIMITE_EMAIL`, `AUDIT_LIMITE_IP` | Rapports envoyés par email et analyses par connexion, par 24 h (défaut : 3 et 10) |
| `DERRIERE_PROXY` | `1` si l'app tourne derrière un proxy (lit `X-Forwarded-For`) |

### Tes réalisations

La page `/realisations` présente tes projets. Pour en ajouter un : mets trois captures dans `app/static/realisations/` (`<nom>-bureau.webp` 1200 × 750, `<nom>-mobile.webp` 360 × 720, `<nom>-complet.webp` page entière) et ajoute une entrée dans la liste `REALISATIONS` de `app/main.py`.

### Ta photo, tes avis clients

- **Photo** : dépose-la dans `app/static/` sous le nom `simon.jpg` (ou `simon.webp`, `simon.png`), carrée, 400 × 400 px environ. Sans photo, tes initiales s'affichent.
- **Avis** : ajoute-les dans `app/avis.json`, uniquement de vrais avis, avec l'accord des clients. Le bloc reste masqué tant que le fichier est vide :

```json
[
  {"texte": "Simon a refait notre site en deux semaines, on reçoit enfin des demandes.", "auteur": "Marie Martin", "entreprise": "Boulangerie Martin"}
]
```

- **Compteur** : « N sites déjà analysés » s'affiche tout seul à partir de 20 audits réels dans la base.

### Brancher Brevo (envoi des emails)

1. Crée un compte sur brevo.com (l'offre gratuite suffit pour démarrer).
2. **Valide ton domaine** : *Expéditeurs, domaines et IP dédiées › Domaines*, ajoute `simondelrue.fr` et copie chez OVH (Zone DNS) les entrées que Brevo affiche (code Brevo, DKIM, DMARC). Sans ça, tes emails risquent de finir en spam.
3. **Ajoute l'expéditeur** `audit@simondelrue.fr` (ou `contact@…`) dans *Expéditeurs*. C'est la valeur de `EXPEDITEUR_EMAIL`.
4. **Crée une clé API** : *SMTP & API › Clés API*. C'est la valeur de `BREVO_API_KEY`.
5. Si Brevo active la restriction par adresse IP sur ton compte (*Sécurité › IP autorisées*), ajoute l'IP de ton serveur, sinon les envois sont refusés.

En cas de souci, les journaux de l'application affichent le message exact renvoyé par Brevo (ligne « Brevo a refusé l'email »).

## Sécurité et RGPD

- **Anti-SSRF** : les adresses internes (localhost, réseaux privés, métadonnées cloud) sont refusées, y compris après une redirection.
- **Anti-abus** : limite par email et par IP, champ piège invisible contre les robots.
- **RGPD** : case de consentement obligatoire, IP stockée hachée (jamais en clair), pages de rapport en `noindex`, mentions légales et politique de confidentialité (`/mentions-legales`, `/confidentialite`).
- **Aucune ressource tierce** : la police Inter est servie par l'application, pas par Google Fonts. La page ne dépose aucun cookie.
- **Référencement de la page elle-même** : `robots.txt`, `sitemap.xml`, adresse canonique et aperçu pour les réseaux sociaux (`app/static/apercu.png`) à partir de `URL_PUBLIQUE`. L'outil, lancé sur sa propre page, obtient 100 dans toutes les catégories.
- Les rapports sont accessibles par un lien non devinable (identifiant aléatoire de 32 caractères).

## Tests

```bash
python3 -m pip install pytest
python3 -m pytest
```

Les tests lancent deux faux sites en local (un bon, un mauvais) et vérifient chaque contrôle, le score, les priorités, le filtrage des réponses de l'IA, les replis, le blocage des adresses internes, les limites et le parcours web complet.

## Mise en ligne

Le projet est livré avec un `Dockerfile` prêt pour Coolify (ou toute plateforme qui sait construire une image Docker) :

- l'application écoute sur le port **8000** ;
- la base SQLite est dans **`/data`** : monte-y un volume persistant, sinon les rapports et les leads disparaissent à chaque redéploiement ;
- `DERRIERE_PROXY=1` est déjà réglé : l'IP du visiteur est lue dans l'en-tête ajouté par le proxy ;
- un healthcheck interroge `/sante`.

Les réglages (clés API, `AUDIT_SEL`, `LIEN_RDV`…) se saisissent dans les variables d'environnement de la plateforme, jamais dans le dépôt.

Sans Docker : `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers`, derrière un reverse proxy (Caddy ou Nginx) pour le HTTPS.

## Limites connues (prochaines étapes)

- Seule la page d'accueil est analysée en profondeur.
- La vitesse est mesurée côté serveur, pas dans un navigateur. Brancher l'API PageSpeed Insights donnerait les Core Web Vitals.
- La détection des bandeaux cookies repose sur une liste d'outils connus : un bandeau fait maison peut ne pas être reconnu.
- Le contrôle anti-SSRF résout le domaine avant la connexion ; un DNS qui change entre-temps (rebinding) n'est pas couvert. Pour une protection complète, faire passer les requêtes sortantes par un proxy filtrant.
- L'audit est synchrone (20 à 40 s). Au-delà de quelques dizaines d'audits par jour, passer à une file de tâches.
