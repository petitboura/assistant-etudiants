# Pivot social — plan de migration séquentiel

But : transformer Djiguigne d'un outil "crée ton agent → obtiens un lien"
vers une plateforme sociale où **l'agent est l'entité vedette** (comme une
Page Facebook), avec profils créateurs, feed de découverte, recherche,
notes, likes et commentaires.

Ce fichier complète `api/PLAN.md` (déjà existant dans `assistant-etudiants`,
étapes 0-6 de la migration Streamlit → API). Il ne le remplace pas : les
décisions d'architecture déjà prises là-bas (auth Supabase JS direct,
backend FastAPI dans `api/`, réutilisation stricte de `core/`) restent
valables. Ce fichier ajoute ce qui change avec le pivot social.

**Règle identique à `api/PLAN.md`** : cocher les cases au fur et à mesure,
noter l'état exact, ne jamais laisser une étape à moitié faite sans note
claire. Lire ce fichier en entier avant de reprendre le travail.

---

## Décision tranchée : repo de la plateforme

**Décidé par Bourama (2026-07-11) — n'est plus bloquant.**

- `djiguigne-frontend` (le site vitrine actuel, `djiguigne-ai.vercel.app`)
  **reste intact, inchangé.** Il continue à vivre tel quel comme site
  marketing (about/blog/contact/services), réutilisé sans y toucher.
- **La plateforme (feed, pages agent, portfolios, dashboard) est un
  nouveau domaine, un nouveau repo Next.js, déployé sur Vercel** —
  confirme l'Option A envisagée plus haut. Ce n'est PAS dans Streamlit :
  seule l'interface de chat reste en Streamlit (voir "Ce qui ne change
  pas").
- **Réutilisation technique autorisée, réutilisation visuelle interdite** :
  la base technique de `djiguigne-frontend` (config Next.js, TypeScript,
  Tailwind, pipeline de déploiement Vercel) est solide et peut servir de
  point de départ/référence pour configurer le nouveau repo. Mais le
  **visuel est à ignorer complètement** — design entièrement nouveau pour
  la plateforme, aucune réutilisation de composants ou de style du site
  vitrine.

---

## Ce qui ne change pas

- Le moteur agent (`core/`, `indexers/`) reste identique : construction du
  `system_prompt`, RAG, appel LLM, outils.
- **L'interface de chat reste en Streamlit**, volontairement (décision de
  Bourama, 2026-07-11) : `faces/vues/chat.py` + `core/main.py` continuent
  de tourner tels quels, app Streamlit multi-agent accessible via
  `?agent=slug`. **Conséquence directe : l'Étape 4 de `api/PLAN.md`
  ("Chat, POST /api/chat, streaming SSE") est abandonnée**, elle ne sert
  plus à rien dans ce plan — le chat ne sera jamais réécrit en Next.js.
- Auth : Next.js parle à Supabase Auth directement, FastAPI vérifie le
  token (`api/auth.py`, déjà fait).
- Le backend API vit dans `assistant-etudiants/api/`, pas de duplication
  de logique métier (décision #3 de `api/PLAN.md`).

Le bouton "Utiliser" (page agent Next.js) ouvre donc l'app Streamlit
existante en popup (iframe pointant vers `URL_RETOUR_APP/?agent=slug`),
et le bouton plein écran ouvre cette même URL en nouvel onglet (pas un
vrai mode plein écran embarqué — juste la même app Streamlit sans le
cadre Next.js autour).

**Point de vigilance à tester tôt (avant de construire l'UI autour) :**
- [ ] Vérifier que l'hébergeur Streamlit (Railway) n'envoie pas de header
      `X-Frame-Options`/`Content-Security-Policy: frame-ancestors` qui
      bloquerait l'iframe depuis le domaine Next.js. Si bloqué : le popup
      devient un simple lien qui ouvre un nouvel onglet directement (pas
      de vraie modale), à valider avec Bourama si ça arrive.

## Ce qui change

- **Thème visuel par agent supprimé.** Toute la personnalisation actuelle
  de `UiConfig` (couleurs, police, rayon des bulles, style de titre
  multicolore, CSS avancé...) disparaît. Un seul thème fixe pour toute la
  plateforme. `core/themes.py` et la majorité des champs de `UiConfig`
  (dans `api/agents.py`) deviennent obsolètes — à retirer, pas juste à
  ignorer, pour ne pas laisser du code mort.
- **L'agent devient une entité publique avec sa propre page**, distincte
  du profil du créateur (avant : un agent n'existait qu'à travers son
  créateur/son lien).
- **Nouveau flow de création** : nom (→ injecté dans le system prompt),
  icône, image/enregistrement de vitrine (modifiable après publication),
  description. La configuration technique (prompt, documents, outils)
  garde la même logique qu'aujourd'hui.
- **Nouvelles notions sociales** : profils créateurs (portfolio), notes de
  1 à 5, commentaires, follow, recherche, feed.

---

## Base technique réutilisable pour le nouveau repo plateforme

Bourama a fourni un extrait déjà filtré de `djiguigne-frontend`
(`djiguigne-frontend-base.zip`) : uniquement les fichiers **techniques**,
zéro fichier visuel (pas de `tailwind.config.ts`, pas de `globals.css`,
pas de composants `Header`/`Footer`/`CookieBanner`). À utiliser comme
point de départ du nouveau repo :

- `app/robots.ts`, `app/sitemap.ts` — génération automatique
- `components/JsonLd.tsx` — injection de données structurées
- `lib/dictionaries.ts` — dictionnaire i18n FR/EN
- `lib/posts.ts` — logique de contenu (à adapter au besoin de la
  plateforme, pas forcément réutilisée telle quelle)
- `lib/site-config.ts` — **source unique de vérité pour les données de
  marque** (nom, mission, contact, dates, statut légal...) — à reprendre
  telle quelle, ne jamais dupliquer ces valeurs en dur ailleurs
- `next.config.mjs`, `postcss.config.mjs`, `tsconfig.json`, `package.json`
  — config de base
- `public/llms.txt`, `public/logo.png`

**Ce qui n'est PAS dans cette base et ne doit PAS être repris** :
tout ce qui touche à l'identité visuelle de `djiguigne-frontend`
(palette `--dj-*` sombre/orange, polices Bricolage Grotesque/Inter/
JetBrains Mono, animations `dj-glow`/`dj-orbit`/`dj-fade-up`). Cette
identité reste **exclusive à la vitrine**, qui n'est pas touchée. La
plateforme aura un visuel entièrement nouveau, à définir séparément.

### Règles SEO/AEO/GEO — techniques, indépendantes du visuel, à appliquer à la plateforme aussi
- Rendu serveur obligatoire (SSR/SSG, App Router) — aucun contenu
  important derrière un onglet/accordéon nécessitant un clic
- `robots.txt` sélectif : bloquer les robots d'entraînement massif
  (GPTBot, CCBot), autoriser les robots de récupération temps réel avec
  attribution (ChatGPT-User, PerplexityBot, ClaudeBot)
- Données structurées JSON-LD (`Organization`, `FAQPage` si pertinent)
- `llms.txt` à la racine (coût nul, pas une priorité)
- Sitemap généré automatiquement (`app/sitemap.ts`), toutes langues
- API Metadata de Next.js (`generateMetadata`) par page, jamais de texte
  statique dans le JSX pour title/description
- Bilingue FR/EN dès le lancement, vraies pages traduites, pas de
  traduction automatique à la volée

### Données de marque à respecter (jamais inventer)
- Nom : Djiguignè AI (accent grave), domaine `djiguigne.com`
- Fondateur : Bourama Diarra, auto-entrepreneur, basé à Tunis, Tunisie
- Contact : boumiservice@gmail.com, +216 54 361 045
- Modèle de prix : **pas encore décidé** — ne jamais afficher de grille
  tarifaire inventée sur la plateforme
- Pages légales obligatoires dès le lancement : mentions légales,
  confidentialité, cookies (statut auto-entrepreneur, hébergement
  Vercel Inc.)
- Aucun analytics à ce jour → pas de bandeau de consentement analytics,
  seulement un bandeau informatif sur le cookie technique utilisé

## Compte unifié — une seule connexion pour toute la plateforme

Décision de Bourama (2026-07-11) : il n'y a pas deux rôles séparés
("créateur" vs "visiteur"). **Toute personne inscrite est fondamentalement
la même chose : un compte qui se connecte une seule fois à la
plateforme.** "Créer un agent" est une action disponible dans le
dashboard, pas un rôle à part — quelqu'un qui n'a jamais créé d'agent est
un compte tout aussi complet que quelqu'un qui en a créé dix.

Conséquence concrète : **les connexions aux outils externes (Notion,
etc.) et la mémoire long-terme des conversations sont scopées par
`user_id` seul, pas par `(user_id, agent_id)`.** Un user connecte son
compte Notion une fois (dans le dashboard, pas dans un agent) et cette
connexion vaut pour n'importe quel agent de la plateforme qui utilise cet
outil. Idem pour la mémoire : elle suit le user d'un agent à l'autre, pas
cloisonnée par agent.

**Ceci reverse deux fixes précédents faits pour la raison inverse :**
- `connexions_notion` avait été volontairement changé de `user_id` seul
  vers `(user_id, agent_id)` pour éviter qu'un agent accède aux données
  Notion connectées pour un AUTRE agent du même user (risque : deux
  agents différents, deux contextes différents, un token partagé qui
  fuit des données non pertinentes d'un contexte à l'autre).
- `conversations`/`conversation_summaries` dans `core/main.py`
  (`_charger_resume_memoire`) sont scopés `(user_id, agent_id)` pour la
  même raison.

**Pourquoi ce revirement n'est pas une régression de sécurité** : la
lecture initiale supposait implicitement "l'agent appartient à son
créateur, ses connexions sont celles du créateur". La nouvelle lecture,
confirmée par Bourama, est différente : ce sont **les données propres du
visiteur** (son compte Notion à lui, sa mémoire de conversation à lui)
qui doivent le suivre partout sur la plateforme, quel que soit l'agent
avec lequel il discute — pas les données du créateur de l'agent qui
fuiteraient vers d'autres agents. C'est le user connecté qui choisit de
connecter ses propres outils, une fois, pour toute la plateforme.

**Portée technique** : ce changement touche `core/main.py`
(`_charger_resume_memoire` et la sauvegarde symétrique) et
`connexions/notion.py`/la table `connexions_notion` — donc du code
Python/Supabase existant à modifier, pas du nouveau code Next.js. À
faire tôt, avant l'Étape C, pour ne pas construire le reste par-dessus un
mauvais scoping.

**Le dashboard existe pour tout le monde dès l'inscription**, pas
seulement pour ceux qui créent un agent — mais il **s'enrichit** dès
qu'un user crée son premier agent (apparition de la section "Mes
agents"). Pas de dashboard séparé selon un "mode créateur" : le même
dashboard gagne des sections, il n'y a jamais de bascule de rôle.

**Chemin de connexion à un outil externe (ex: Notion)** :
- Chemin normal : depuis le dashboard, indépendamment de tout agent.
- Fallback en contexte : si un user utilise un agent qui a besoin d'un
  outil pas encore connecté, il peut le connecter directement depuis cet
  agent (pas obligé de d'abord aller au dashboard) — la connexion créée
  rejoint ensuite son compte unifié (`user_id` seul, voir ci-dessus) et
  vaut pour tous les autres agents.

## Limite pour visiteur non connecté (lien partagé)

Décision de Bourama (2026-07-11) : quelqu'un qui reçoit un lien vers un
agent (ex: un ami qui le lui envoie) et l'ouvre **sans être inscrit** peut
discuter librement jusqu'à **4 messages** (entre 3 et 5, valeur à ajuster
facilement — pas un chiffre figé), puis se fait bloquer avec un message
qui invite à s'inscrire sur la plateforme (lien d'invitation). Sans
compte, pas de mémoire long-terme ni de connexions d'outils qui suivent
(cohérent avec "Compte unifié" ci-dessus, qui ne s'applique qu'aux
comptes connectés).

**Portée technique** : comme le chat reste en Streamlit
(`faces/vues/chat.py`), ce comptage se fait côté Streamlit — compteur de
messages dans `st.session_state` quand `user_id` est absent (visiteur non
connecté), blocage de l'input après le seuil, affichage du lien
d'inscription (`URL_RETOUR_APP/inscription` ou équivalent une fois le
frontend Next.js en place).

---

## Modèle de données Supabase — changements

### Table `agents` (existante, à modifier)
- Retirer : la quasi-totalité de `ui_config` (couleurs/police/CSS —
  garder uniquement `icone_page`)
- Ajouter :
  - `image_vitrine_url` (text) — image ou capture d'écran affichée sur la
    page agent, modifiable
  - `description` (text) — texte de présentation, distinct de
    `knowledge_source.description` (usage interne RAG)
  - `slug` (text, unique) — pour l'URL publique de la page agent

### Table `profiles` (nouvelle)
- `user_id` (uuid, PK, FK → `auth.users`)
- `nom_affiche` (text)
- `bio` (text)
- `avatar_url` (text)
- `slug` (text, unique) — pour l'URL publique du portfolio

### Table `agent_ratings` (nouvelle)
- `agent_id` (FK → agents), `user_id` (FK → auth.users), `note` (int, 1-5)
- Contrainte unique `(agent_id, user_id)` — un user note un agent une fois
  (modifiable, pas cumulable)

### Table `agent_comments` (nouvelle)
- `id`, `agent_id` (FK), `user_id` (FK), `contenu` (text), `created_at`

### Table `follows` (nouvelle)
- `follower_id` (FK → auth.users), `creator_id` (FK → auth.users)
- Contrainte unique `(follower_id, creator_id)`

**Note** : la recherche (nom d'agent ou de créateur → redirection) peut
s'appuyer sur un index simple sur `agents.nom`/`agents.slug` et
`profiles.nom_affiche`/`profiles.slug` dans un premier temps ; pas besoin
de moteur de recherche dédié pour une v1.

---

## Pages de l'app (frontend Next.js)

| Route (indicative) | Accès | Contenu |
|---|---|---|
| `/` (feed) | Public | Barre de recherche + découverte des pages agents |
| `/agent/[slug]` | Public, lien partageable | Icône, nom, image vitrine, description, bouton "Utiliser" (popup chat → plein écran), note 1-5, commentaires, lien vers le portfolio du créateur |
| `/u/[slug]` (portfolio) | Public, lien partageable | Liste des agents du créateur, bouton Follow |
| `/dashboard` | Privé (tout compte connecté) | Édition du profil + connexion des outils externes. Section "Mes agents" apparaît dès la création du premier agent — pas de dashboard séparé selon un rôle |
| `/dashboard/agents/nouveau` | Privé | Nouveau flow de création d'agent |
| `/inscription`, `/connexion` | Public | Supabase Auth JS direct |

---

## Étapes séquentielles

### Étape A — Décision bloquante
- [ ] Trancher Option A vs B (voir section ci-dessus)

### Étape B — Migration schéma Supabase
**FAIT le 2026-07-11** (migration Supabase `pivot_social_etape_b_tables`,
projet `rwcyeppxfonvqbvztxyg`), vérifié par requête après coup.
- [x] Créer `profiles`, `agent_ratings`, `agent_comments`, `follows`
      (RLS activé sur les 4, SANS policy — cohérent avec `agents`/
      `connexions_notion` qui n'ont pas de policy non plus : le backend
      FastAPI utilise la service role key et gère les autorisations en
      Python, jamais via des policies RLS. Ne pas ajouter de policies à
      la légère, ça romprait la convention du projet)
- [x] Modifier `agents` : ajouter `image_vitrine_url` (text, nullable),
      `description` (text, nullable)
- [x] Pas de colonne `slug` ajoutée sur `agents`, et pas de script de
      backfill nécessaire : vérifié en base, `agents.id` est déjà un
      identifiant lisible généré via `generer_id_depuis_nom` (ex.
      `tutorat-maths`, `business`) — il sert déjà de slug pour l'URL
      publique `/agent/[id]`. Le plan initial en prévoyait un par erreur.
- [ ] `ui_config` PAS ENCORE nettoyé (colonnes couleurs/police/CSS encore
      présentes en base et dans `api/agents.py`) — volontairement laissé
      pour une étape à part, pas fait ici pour rester sur un changement
      ciblé. Prochaine IA : ne pas les retirer sans relire d'abord
      `api/agents.py` (`UiConfig`) et `faces/vues/creer_agent.py` pour
      voir tout ce qui les lit avant de casser quelque chose.
- [ ] `profiles.slug` : colonne créée mais **rien ne la remplit encore**
      — pas de code Python qui génère ce slug à l'inscription. À faire à
      l'Étape D (frontend) ou en trigger Supabase, pas décidé encore.

**Prochaine étape à reprendre : Étape B.2** (déscoper `connexions_notion`
et la mémoire par agent) — PAS COMMENCÉE, nécessite d'abord de vérifier
s'il y a des données existantes en conflit (plusieurs lignes par
`user_id` avec des `agent_id` différents) avant de retirer `agent_id` de
la clé primaire, sans quoi la migration échouera ou perdra des données
silencieusement. Vérifier avec un `SELECT user_id, count(*) FROM
connexions_notion GROUP BY user_id HAVING count(*) > 1` (et l'équivalent
sur `conversation_summaries`) avant de toucher au schéma.

### Étape B.2 — Déscoper les connexions et la mémoire par agent
**Volet Supabase FAIT le 2026-07-11** (migration
`pivot_social_etape_b2_descope_user_id`, projet `rwcyeppxfonvqbvztxyg`),
vérifié avant et après coup — aucune ligne en conflit trouvée
(`connexions_notion` avait 1 ligne, `conversation_summaries` 0 ligne au
moment de la migration, donc aucune perte de données possible).
- [x] `connexions_notion` : colonne `agent_id` supprimée, clé primaire
      redevenue `user_id` seul
- [x] `conversation_summaries` : colonne `agent_id` supprimée, clé
      primaire redevenue `user_id` seul
- [x] `conversations` (le log brut des messages) volontairement PAS
      touché : sa PK est déjà `id` seul, `agent_id` y reste comme simple
      métadonnée de traçabilité, ne pilote pas le comportement mémoire

**Volet code Python FAIT le 2026-07-11** — signature choisie : `agent_id`
retiré partout (option "propre"), pas gardé en paramètre ignoré.
- [x] `connexions/notion.py` : `obtenir_token_valide(user_id)` et
      `est_connecte(user_id)` désscopés (signature + requêtes) ;
      `_rafraichir` et l'upsert de `finaliser_connexion_notion` ne
      référencent plus `agent_id` (`on_conflict="user_id"`).
      `demarrer_connexion_notion(user_id, agent_id)` garde `agent_id` :
      il sert uniquement à `notion_oauth_temp` (non touché par la
      migration) pour savoir vers quel agent rediriger après le retour
      OAuth, pas à scoper l'accès obtenu.
- [x] `core/main.py` : `_charger_resume_memoire(user_id)` et
      `_mettre_a_jour_resume_si_besoin(user_id)` désscopés, y compris le
      filtre sur `conversations` (qui garde la colonne `agent_id` comme
      métadonnée mais ne filtre plus dessus). `_sauvegarder_echange`
      inchangé : il continue d'écrire `agent_id` dans `conversations`
      (simple traçabilité, cohérent avec l'entrée Étape B ci-dessus).
      3 sites d'appel dans `chat()` mis à jour.
- [x] `core/registre_outils.py` (`_headers_notion`) et
      `faces/vues/chat.py` (`est_connecte`, message UI) mis à jour en
      conséquence — tous les fichiers touchés compilent sans erreur.
- [x] Testé en conditions réelles : reste à faire par Bourama au prochain
      déploiement Railway (pas testable depuis cette session), mais plus
      aucune requête ne référence une colonne `agent_id` inexistante sur
      `connexions_notion`/`conversation_summaries`.

**Étape B.2 terminée.**

### Étape B.3 — Limite visiteur non connecté
**Code écrit le 2026-07-11, poussé sur `main` par Bourama (upload manuel,
le token GitHub fourni avait cessé de fonctionner) — confirmé présent sur
le repo.**
- [x] Constante `SEUIL_VISITEUR_NON_CONNECTE = 4` ajoutée (facilement
      ajustable entre 3 et 5), avec `st.session_state.compteur_visiteur`
      initialisé à côté des autres compteurs de session
- [x] `user_id_courant` déplacé plus haut dans le fichier (calculé avant
      le rendu du chat input, plutôt qu'à l'intérieur du bloc de
      traitement) pour pouvoir décider de bloquer AVANT d'afficher
      l'input
- [x] Passé le seuil (et uniquement si `user_id_courant is None`) :
      message d'invitation à s'inscrire + `st.link_button` vers
      `URL_RETOUR_APP/inscription` (affiché seulement si ce secret est
      configuré, pas de lien cassé sinon) + `st.chat_input(...,
      disabled=True)` pour garder la barre visible mais inutilisable
- [x] Le compteur existant `st.session_state.compteur` (déclenche le
      formulaire de feedback à 3 messages, mécanisme différent et non
      lié à Étape B.3) laissé inchangé
- [ ] **Reste à faire** : tester en conditions réelles sur le déploiement
      Railway (notamment vérifier que `st.chat_input(disabled=True)` est
      bien supporté par la version de Streamlit déployée — pas testable
      depuis cette session)

**Étape B.3 terminée (code).** Prochaine étape au choix de Bourama :
Étape C (backend API) ou test en conditions réelles des Étapes B.2/B.3.

### Étape C — Backend API (dans `assistant-etudiants/api/`)
- [x] `POST /api/agents` : adapté au nouveau payload le 2026-07-11 —
      `UiConfig` réduit à `icone_page` seul (tous les champs de thème
      retirés, pas juste ignorés), `image_vitrine_url` et `description`
      ajoutés au payload et écrits en base. `ui_config_dict` ne calcule
      plus que `titre_page`/`icone_page`/`titre_accueil`/`emoji_reponse` ;
      tout le reste retombe sur `UI_CONFIG_PAR_DEFAUT` côté
      `faces/vues/chat.py` (aucune modif nécessaire là-bas). **Poussé sur
      `main` par Bourama (upload manuel), vérifié identique sur le repo.**
- [x] `GET /api/feed` : **confirmé présent et identique sur `main`**
      (relu depuis un clone frais du repo le 2026-07-11) — route publique
      (`GET /api/feed?page=1&limite=20`, pagination offset plafonnée à
      50/page), liste les agents où `actif` est `True` OU absent/NULL
      (même convention que `_agent_est_actif` dans `chat.py`, pour ne pas
      faire disparaître les agents créés avant l'ajout de la colonne
      `actif`). Retourne `id`, `nom`, `icone_page`, `image_vitrine_url`,
      `description`. Testé en local avec `TestClient` (Supabase factice) :
      démarre sans erreur d'import, route atteinte, gère l'erreur Supabase
      proprement (500 propre, pas de crash) — pas testé avec une vraie
      base Supabase.
- [ ] `GET /api/agents/{slug}` : **code écrit le 2026-07-11 dans
      `api/agents.py` (`GET /api/agents/{agent_id}`), PAS ENCORE POUSSÉ
      sur `main`** — public, aucune auth. Retourne `id`, `nom`,
      `icone_page`, `image_vitrine_url`, `description`, `owner_id` (ce
      dernier pour préparer le lien vers le portfolio créateur à l'Étape
      E, sans résoudre le profil ici). 404 si agent introuvable OU
      `actif` est `False` explicitement (même convention "True par
      défaut" que `/api/feed` et `_agent_est_actif`). Testé en local avec
      `TestClient` (Supabase factice) : démarre sans erreur d'import,
      route atteinte, 500 propre sur erreur Supabase (pas de crash) — pas
      testé avec une vraie base Supabase ni un vrai agent
      actif/désactivé. **À uploader (token GitHub toujours invalide, voir
      Changelog) puis tester en conditions réelles avant de cocher.**
- [ ] `PATCH /api/agents/{id}/vitrine` : **code écrit le 2026-07-11 dans
      `api/agents.py`, PAS ENCORE POUSSÉ sur `main`** — mise à jour
      partielle (`image_vitrine_url` et/ou `description`, champ omis =
      pas touché). Auth requise ; vérifie que `owner_id` correspond au
      token (403 sinon, même exigence que celle notée pour l'Étape 2 de
      `api/PLAN.md`). Testé en local avec `TestClient` : sans token → 401
      (dépendance d'auth déclenchée avant toute logique métier) — pas
      testé avec un vrai token ni une vraie écriture en base (mêmes
      limites que les endpoints précédents). **À uploader puis tester en
      conditions réelles avant de cocher.**
- [ ] `POST /api/agents/{id}/rating`, `GET /api/agents/{id}/rating` :
      **code écrit le 2026-07-11 dans `api/agents.py`, PAS ENCORE POUSSÉ
      sur `main`** — `POST` fait un upsert sur `agent_ratings`
      (`agent_id`, `user_id`, `note`), respecte la contrainte unique
      `(agent_id, user_id)` de la table (voir section "Modèle de
      données") : un utilisateur peut modifier sa note, pas la cumuler.
      Valide `note` entre 1 et 5 (422 sinon), pas de vérification
      d'existence de l'agent (repose sur la contrainte FK). `GET` public,
      renvoie `moyenne` (None si aucune note) et `total`. Testé en local :
      sans token → 401 sur `POST` (avant même la validation de `note`,
      l'auth passe en premier) ; `GET` atteint la DB et gère l'erreur
      proprement (500 propre sur Supabase factice). Pas testé avec une
      vraie base.
- [ ] `POST /api/agents/{id}/comments`, `GET /api/agents/{id}/comments` :
      **code écrit le 2026-07-11 dans `api/agents.py`, PAS ENCORE POUSSÉ
      sur `main`** — `POST` insère dans `agent_comments`, 422 si contenu
      vide, aucune limite de fréquence/modération pour l'instant. `GET`
      public, paginé (mêmes bornes que `/api/feed`, limite plafonnée à
      50/page), triés par `created_at` décroissant. Testé en local :
      `POST` sans token → 401 ; `GET` atteint la DB (500 propre sur
      Supabase factice) ; validation de pagination (`page=0` → 422)
      vérifiée. Pas testé avec une vraie base.
- [ ] `POST /api/creators/{id}/follow`, `DELETE .../follow` : **code
      écrit le 2026-07-11, dans un nouveau fichier `api/creators.py`
      (pas `api/agents.py` — ces routes portent sur un créateur, pas un
      agent), PAS ENCORE POUSSÉ sur `main`**. Les deux sont idempotents :
      `POST` fait un upsert sur `follows` (contrainte unique
      `(follower_id, creator_id)`, suivre deux fois ne renvoie pas de
      409) ; `DELETE` ne renvoie pas d'erreur si le follow n'existait pas.
      422 si `creator_id` == l'utilisateur courant (impossible de se
      suivre soi-même). Nouveau routeur enregistré dans `api/main.py`
      (`creators_router`, prefix `/api/creators`). Testé en local : sans
      token → 401 sur les deux routes. Pas testé avec un vrai token ni
      une vraie écriture en base.
- [ ] `GET /api/profiles/{slug}`, `PATCH /api/profiles/me` : **code écrit
      le 2026-07-11 dans un nouveau fichier `api/profiles.py`, PAS ENCORE
      POUSSÉ sur `main`**. Décision prise faute de réponse tranchée de
      Bourama sur la génération de `profiles.slug` : ces routes
      utilisent **`user_id` directement comme clé d'URL**
      (`/api/profiles/{user_id}`, pas `{slug}`), même repli que celui
      déjà fait pour `agents.id`. À l'inverse du cas `agents.id`
      cependant, rien n'existait déjà à réutiliser ici (juste un choix
      pragmatique, pas une redécouverte d'une valeur déjà présente en
      base) — **à revalider avec Bourama**, et à remplacer par un vrai
      slug quand sa génération sera décidée (changement d'URL, pas une
      simple substitution de colonne). `GET` : portfolio public, inclut
      la liste des agents actifs du créateur (best-effort : l'échec de
      cette sous-requête n'empêche pas de renvoyer le profil), 404 si
      aucun profil n'existe pour ce `user_id`. `PATCH /me` : upsert (pas
      update seul), car rien ne garantit qu'une ligne `profiles` existe
      déjà — sert aussi de création de profil au premier appel. Testé en
      local : `GET` atteint la DB (500 propre sur Supabase factice),
      `PATCH` sans token → 401. Pas testé avec une vraie base.
- [x] `GET /api/search?q=...` : **code écrit le 2026-07-11 dans un
      nouveau fichier `api/search.py`, PAS ENCORE POUSSÉ sur `main`** —
      recherche `ilike` sur `agents.nom` et `profiles.nom_affiche`
      (identifiants renvoyés : `agents.id` et `profiles.user_id`, même
      logique que ci-dessus pour les créateurs), 20 résultats max par
      catégorie, aucune pagination/scoring pour cette v1 (conforme à la
      note de PIVOT_SOCIAL.md : pas de moteur dédié nécessaire). **Chaque
      sous-recherche est best-effort** : si `agents` ou `profiles` échoue
      côté Supabase, cette catégorie renvoie une liste vide plutôt qu'une
      500 globale — comportement volontairement différent des autres
      endpoints (qui échouent fort), à valider avec Bourama si ce n'est
      pas le comportement voulu pour une recherche. Testé en local : sans
      `q` → 422 (validation), avec `q` → 200 avec listes vides (Supabase
      factice, erreurs absorbées comme prévu). Pas testé avec une vraie
      base ni de vraies données à retrouver.
- [ ] Reprend l'Étape 2 (upload documents) de `api/PLAN.md`, inchangée
      par le pivot. L'Étape 4 (chat SSE) de `api/PLAN.md` est abandonnée
      (voir "Ce qui ne change pas" — le chat reste en Streamlit)

### Étape D — Frontend : squelette + auth
- [ ] Setup du repo choisi à l'Étape A, Supabase Auth JS (inscription/
      connexion), stockage du token, appel `GET /health/me` pour valider

### Étape E — Frontend : pages publiques
- [ ] Feed (`/`) avec recherche
- [ ] Page agent (`/agent/[slug]`) avec popup chat → plein écran
- [ ] Portfolio créateur (`/u/[slug]`)

### Étape F — Frontend : dashboard privé
- [ ] Mes agents (liste, édition, image vitrine, description)
- [ ] Nouveau flow de création d'agent
- [ ] Édition du profil

### Étape G — Bascule finale
- [ ] Décommissionner les vues Streamlit remplacées par le Next.js
      (`app_etudiant.py` en tant que hub, `creer_agent.py`, `mes_agents.py`,
      `vitrine.py`) une fois tout validé en conditions réelles — **sauf
      `faces/vues/chat.py`, qui reste en production indéfiniment** (voir
      "Ce qui ne change pas"). Cette étape diffère donc de l'Étape 6
      originale de `api/PLAN.md`, qui prévoyait de retirer tout Streamlit.

---

## Changelog

- 2026-07-11 — Plan initial écrit suite au pivot vers une plateforme
  sociale (profils créateurs, pages agents publiques, feed, recherche,
  notes 1-5, commentaires, follow). Décision bloquante identifiée : choix
  du repo frontend (nouveau repo app vs `djiguigne-frontend` existant).
  Aucun code écrit à ce stade.
- 2026-07-11 — Décision de Bourama : l'interface de chat reste en
  Streamlit définitivement (pas de réécriture en Next.js). L'Étape 4 de
  `api/PLAN.md` (chat SSE) est abandonnée. Le bouton "Utiliser" sur la
  page agent Next.js ouvre l'app Streamlit existante en iframe/popup,
  puis en nouvel onglet pour le plein écran. Point de vigilance ajouté :
  vérifier les headers anti-iframe de l'hébergeur Streamlit avant de
  construire l'UI du popup.
- 2026-07-11 — Décision de Bourama : compte unique et unifié pour toute la
  plateforme, pas de rôle "créateur" vs "visiteur" séparé. Connexions aux
  outils externes et mémoire long-terme reversées de `(user_id, agent_id)`
  vers `user_id` seul — annule les scopings précédents faits dans
  `connexions_notion` et `core/main.py:_charger_resume_memoire`, pour la
  raison inverse de l'époque (voir section "Compte unifié"). Nouvelle
  Étape B.2 ajoutée, à faire avant l'Étape C.
- 2026-07-11 — Précision de Bourama : le dashboard existe pour tout
  compte dès l'inscription (pas réservé aux créateurs), il s'enrichit
  simplement de la section "Mes agents" une fois le premier agent créé.
  Connexion aux outils externes : chemin normal = dashboard, mais
  possible aussi directement depuis un agent si l'outil n'est pas encore
  connecté (fallback en contexte).
- 2026-07-11 — Décision de Bourama : visiteur non connecté arrivant via
  un lien partagé peut discuter jusqu'à 4 messages (3-5, ajustable) avant
  blocage + lien d'inscription. Nouvelle Étape B.3 ajoutée.
- 2026-07-11 — Décision tranchée : `djiguigne-frontend` (vitrine) reste
  intact et inchangé, réutilisé tel quel comme site marketing. La
  plateforme (feed, pages agent, portfolios, dashboard) est un nouveau
  repo Next.js sur Vercel, distinct — confirme l'Option A. Réutilisation
  technique de `djiguigne-frontend` autorisée (config Next.js/TS/Tailwind/
  déploiement), réutilisation visuelle interdite (design entièrement
  nouveau). La section "Décision à trancher (bloquant)" est remplacée par
  "Décision tranchée".
- 2026-07-11 — Bourama a fourni une base technique filtrée de
  `djiguigne-frontend` (`djiguigne-frontend-base.zip`, sans aucun fichier
  visuel) comme point de départ du nouveau repo plateforme. Ajout des
  règles SEO/AEO/GEO et des données de marque officielles, tirées de la
  documentation Notion fournie — l'identité visuelle de cette même
  documentation (thème sombre/orange, animations) est explicitement
  exclue, réservée à la vitrine.
- 2026-07-11 — Étape B terminée : migration Supabase appliquée
  (`pivot_social_etape_b_tables`) — tables `profiles`, `agent_ratings`,
  `agent_comments`, `follows` créées (RLS activé, sans policy, cohérent
  avec le reste du projet) ; `agents` a gagné `image_vitrine_url` et
  `description`. Pas de colonne `slug` sur `agents` : `id` sert déjà de
  slug. `ui_config` volontairement pas nettoyé (étape à part).
  `profiles.slug` pas encore rempli par du code. Prochaine étape :
  Étape B.2, en vérifiant d'abord s'il existe des lignes en conflit
  (plusieurs `agent_id` par `user_id`) dans `connexions_notion` et
  `conversation_summaries` avant de changer la clé primaire.
- 2026-07-11 — Session de reprise : relu le fichier depuis GitHub (pas
  depuis la mémoire de conversation) pour valider que le handoff
  fonctionne — identique à la copie locale. Étape B.2 commencée : vérifié
  l'absence de lignes en conflit, puis migration Supabase appliquée
  (`pivot_social_etape_b2_descope_user_id`) — `agent_id` supprimé de
  `connexions_notion` et `conversation_summaries`, clé primaire = `user_id`
  seul sur les deux. **Le code Python (`connexions/notion.py`,
  `core/main.py`) n'est PAS encore à jour** — le schéma a changé avant le
  code, donc ces fonctionnalités sont cassées jusqu'à la prochaine
  session. Prochaine étape : corriger le code Python avant de toucher à
  autre chose.
- 2026-07-11 — Étape B.2 terminée (volet code Python). Décision de
  Bourama sur la signature : `agent_id` retiré partout plutôt que gardé
  en paramètre ignoré. `connexions/notion.py`
  (`obtenir_token_valide`/`est_connecte`/`_rafraichir`/upsert de
  `finaliser_connexion_notion`), `core/main.py`
  (`_charger_resume_memoire`/`_mettre_a_jour_resume_si_besoin`, y compris
  le filtre sur `conversations`), `core/registre_outils.py`
  (`_headers_notion`) et `faces/vues/chat.py` (`est_connecte`, message
  UI) mis à jour et commités sur `main`
  (`dc66975`, `fd29309`, `046ac71`, `e1f830e`). `demarrer_connexion_notion`
  garde `agent_id` (usage : redirection post-OAuth via `notion_oauth_temp`,
  non touché par la migration) — pas de régression, juste une utilisation
  différente du même paramètre. `_sauvegarder_echange` inchangé
  (`agent_id` reste une métadonnée de traçabilité dans `conversations`).
  Code non testé en conditions réelles (pas de déploiement Railway depuis
  cette session) — à valider par Bourama au prochain déploiement.
- 2026-07-11 — Étape B.3 (limite visiteur non connecté) : code écrit dans
  `faces/vues/chat.py` — seuil `SEUIL_VISITEUR_NON_CONNECTE = 4`,
  compteur `st.session_state.compteur_visiteur`, blocage de l'input +
  lien d'inscription. **PAS COMMITÉ** : le token GitHub fourni par
  Bourama a cessé de fonctionner (401, probablement révoqué juste après
  son usage pour l'Étape B.2 — bon réflexe côté sécurité) avant ce
  commit. Fichier remis en téléchargement à Bourama à la place. Prochaine
  étape : appliquer ce fichier sur le repo (nouveau token ou upload
  manuel), le tester, puis reprendre soit l'Étape C (backend API) soit
  une nouvelle vérification en conditions réelles de l'Étape B.2.
- 2026-07-11 — Confirmé : Bourama a uploadé manuellement `chat.py` (Étape
  B.3) et `PIVOT_SOCIAL.md` sur `main`, vérifié par lecture directe du
  repo (contenu identique à celui préparé). Étape B.3 marquée terminée
  (code) ; reste le test en conditions réelles sur Railway.
- 2026-07-11 — Début Étape C. `POST /api/agents` adapté (`api/agents.py`)
  et poussé sur `main` par upload manuel de Bourama, vérifié identique
  sur le repo. `GET /api/feed` écrit dans `api/main.py` (route publique,
  pagination, agents `actif` True/NULL) mais **pas encore confirmé
  poussé** — Bourama a demandé de mettre à jour ce fichier plan avant de
  finaliser l'upload, donc l'état "poussé" de `GET /api/feed` reste à
  vérifier à la prochaine session avant de le cocher. Nouvelle tentative
  du token GitHub fourni précédemment : toujours 401 Bad credentials,
  invalide/révoqué, pas un problème de rate limit — un nouveau token
  serait nécessaire pour que l'IA commite directement à l'avenir.
- 2026-07-11 — Reprise de session : clone frais du repo, confirmation que
  `GET /api/feed` est bien présent et identique sur `main` (coché).
  `GET /api/agents/{agent_id}` (détail public d'un agent) écrit dans
  `api/agents.py`, testé en local (`TestClient`, Supabase factice) :
  import et routing OK, erreurs gérées proprement. Toujours pas
  d'accès en écriture au dépôt (token GitHub invalide, confirmé à
  nouveau : `git push` échoue faute d'identifiants configurés) —
  fichier à uploader manuellement par Bourama. Prochaine étape une fois
  uploadé et testé en conditions réelles : `PATCH /api/agents/{id}/vitrine`
  ou un nouveau token GitHub pour débloquer les commits directs.
- 2026-07-11 — Suite de session : `PATCH /api/agents/{agent_id}/vitrine`
  écrit dans `api/agents.py` (mise à jour partielle image/description,
  vérification `owner_id`, 403 sinon). Testé en local : sans token → 401.
  Toujours pas d'accès en écriture au dépôt. Prochaine étape une fois
  uploadé et testé : `POST/GET /api/agents/{id}/rating` ou
  `POST/GET /api/agents/{id}/comments`.
- 2026-07-11 — Suite de session : `POST/GET /api/agents/{id}/rating`
  (upsert note 1-5, contrainte unique respectée) et
  `POST/GET /api/agents/{id}/comments` (insertion + liste paginée)
  écrits dans `api/agents.py`. Testés en local (auth 401 sans token sur
  les deux `POST`, validation pagination sur `GET comments`, erreurs
  Supabase gérées proprement). Toujours pas d'accès en écriture au
  dépôt. Reste dans l'Étape C : `POST/DELETE /api/creators/{id}/follow`,
  `GET /api/profiles/{slug}`, `PATCH /api/profiles/me`,
  `GET /api/search`.
- 2026-07-11 — Suite de session : `POST/DELETE /api/creators/{id}/follow`
  écrits dans un nouveau fichier `api/creators.py` (upsert/delete
  idempotents sur `follows`), routeur enregistré dans `api/main.py`.
  Testé en local : 401 sans token sur les deux routes. Arrêt volontaire
  avant `GET /api/profiles/{slug}` : bloqué sur la génération de
  `profiles.slug`, non tranchée (voir note Étape B) — décision à prendre
  avec Bourama avant de continuer sur ce point, pas de choix fait seul.
  Toujours pas d'accès en écriture au dépôt.
- 2026-07-11 — Suite de session : Bourama a demandé de continuer sans
  trancher la question posée sur `profiles.slug`. Décision prise par
  défaut (à revalider) : `GET /api/profiles/{user_id}` et
  `PATCH /api/profiles/me` écrits dans un nouveau fichier
  `api/profiles.py`, en utilisant `user_id` comme clé d'URL en attendant
  une vraie génération de slug. `GET /api/search?q=...` écrit dans
  `api/search.py` (recherche `ilike` sur agents + créateurs, best-effort
  par catégorie). Routeurs `profiles_router` et `search_router`
  enregistrés dans `api/main.py`. Tous testés en local (401 sans token,
  422 sur validation manquante, erreurs Supabase absorbées proprement).
  **L'Étape C du pivot social est maintenant entièrement écrite**
  (aucun endpoint restant dans la checklist), mais rien n'est poussé sur
  `main` (token GitHub toujours invalide) ni testé avec une vraie base
  Supabase. Prochaine étape : upload manuel de tous les fichiers de cette
  étape, tests en conditions réelles, puis Étape D (frontend Next.js).
- 2026-07-11 — Reprise de session : Étape C confirmée poussée sur `main`
  (vérifié par lecture directe du repo, `api/agents.py`, `api/creators.py`,
  `api/profiles.py`, `api/search.py`, `api/main.py` tous présents et
  identiques à ce qui avait été préparé) — uploadée manuellement par
  Bourama entre-temps, pas dans une session précédente documentée ici.
  **Clarification importante de Bourama sur la réutilisation visuelle**
  (reformule la section "Base technique réutilisable" ci-dessus, qui
  prêtait à confusion) : l'interdiction de réutilisation ne portait PAS
  sur le thème visuel (palette `--dj-*`, typographie Bricolage Grotesque/
  Inter/JetBrains Mono, dégradés, animations, logo) — celui-là EST repris
  à l'identique pour la plateforme. L'interdiction porte sur la
  STRUCTURE : les pages de la vitrine (accueil marketing, services, blog,
  about, contact, FAQ) et leurs composants (`Header`/`Footer`/
  `CookieBanner`/`LanguageSwitcher`, nav, i18n par route `[locale]`) ne
  sont PAS repris — la plateforme a sa propre structure de pages (feed,
  page agent, portfolio, dashboard...). Prochaine IA : même thème,
  structure différente, pas de blog/FAQ/services sur la plateforme.
  **Découpage de l'Étape D en sous-étapes séquentielles** (décision de
  Bourama, pour permettre une reprise par une autre IA si limite
  atteinte) :
  - [x] **D.1 — Fondations visuelles** : `tailwind.config.ts` (palette
        `dj-*`, `dj-gradient`, `dj-hero-glow`, polices, keyframes/
        animations `dj-fade-up`/`dj-fade-in`/`dj-orbit`/`dj-glow`),
        `app/globals.css` (variables CSS, fond+glow, sélection, focus
        visible, scrollbar, `.dj-reveal`, `prefers-reduced-motion`),
        `app/layout.tsx` (chargement des 3 polices via `next/font/google`,
        classes de thème sur `<html>`/`<body>`), `public/logo.png` copié
        depuis `djiguigne-frontend`. Pages existantes du squelette
        (`/connexion`, `/inscription`, `/`) restylées avec les tokens
        `dj-*` pour repartir sur une base cohérente avant de construire
        les nouvelles pages dessus. Vérifié : `tsc --noEmit` sans erreur.
        `next build` PAS vérifiable dans l'environnement de préparation
        (pas d'accès réseau à Google Fonts depuis ce sandbox) — à tester
        en conditions réelles par Bourama (`npm run dev` ou build Vercel).
        Fichier remis en téléchargement (zip du dossier `frontend/`), pas
        poussé sur GitHub (pas d'accès en écriture, comme d'habitude).
  - [x] **D.2 — Feed** (`/`) : `/` transformé en feed PUBLIC — changement
        important par rapport à D.1, où `/` exigeait une session et
        redirigeait vers `/connexion` (ce comportement était temporaire,
        juste pour prouver que l'auth passait bien de bout en bout ; ce
        rôle est rempli, cette page n'existe plus telle quelle). Ajouts :
        `components/TopBar.tsx` (nav minimale de la plateforme, PAS le
        Header vitrine — pas de services/blog/about/contact ; état auth
        via `supabase.auth.onAuthStateChange`, lien "Mon espace" vers
        `/dashboard` déjà posé même si la page n'existe pas encore),
        `components/AgentCard.tsx` (carte agent réutilisable, prévue pour
        resservir en D.4 portfolio), recherche débouncée (300ms, min 2
        caractères, annule une requête en vol si une frappe plus récente
        arrive) sur `GET /api/search`, grille + pagination Précédent/
        Suivant sur `GET /api/feed`. `next.config.mjs` : `remotePatterns`
        ajouté pour autoriser `*.supabase.co` (next/image refuse par
        défaut tout domaine non déclaré, nécessaire pour
        `image_vitrine_url`). Vérifié : `tsc --noEmit` sans erreur.
        `next build` toujours pas vérifiable ici (Google Fonts hors
        réseau autorisé du sandbox) — à tester par Bourama.
  - [x] **D.3 — Page agent** (`/agent/[id]`) : Server Component pour le
        SSR (règle SEO/AEO/GEO du plan), `generateMetadata` (title/
        description/OG image depuis l'agent), `notFound()` si l'agent
        n'existe pas ou est désactivé (même convention 404 que le
        backend). Consomme `GET /api/agents/{id}` via un nouveau
        `lib/api-serveur.ts` (fetch simple, PAS `appelerApi` de
        `lib/api.ts` — celui-ci appelle `supabase.auth.getSession()`, qui
        lit le localStorage du navigateur, indisponible côté serveur ;
        `api-serveur.ts` est donc réservé aux endpoints publics
        uniquement). Composants clients isolés par-dessus le rendu
        serveur : `BoutonUtiliser` (popup iframe vers l'app Streamlit
        `?agent=id` + bouton plein écran en nouvel onglet — nouvelle
        variable d'env `NEXT_PUBLIC_STREAMLIT_URL` ajoutée à
        `.env.local.example`, doit matcher `URL_RETOUR_APP` côté
        Streamlit), `NoteAgent` (étoiles 1-5, upsert via
        `POST .../rating`), `CommentairesAgent` (liste + formulaire,
        `POST .../comments`). Lien vers le portfolio créateur en
        `/u/{owner_id}` (404 attendu tant que D.4 n'existe pas — même
        convention que `api/profiles.py`, qui utilise `user_id` en
        attendant une vraie génération de slug).
        **Non vérifié : le point de vigilance iframe/X-Frame-Options
        noté plus haut dans ce fichier** (toujours pas testé contre un
        vrai déploiement Streamlit).
        Vérifié : `tsc --noEmit` sans erreur. `next build` échoue
        toujours uniquement sur le fetch des Google Fonts (réseau
        indisponible dans ce sandbox, même limite que D.1/D.2, pas un
        bug introduit ici) — à tester par Bourama.
  - [x] **D.4 — Portfolio créateur** (`/u/[id]`, en pratique `user_id`,
        pas un slug — même repli que `/agent/[id]` et que le backend
        `api/profiles.py`) : Server Component + `generateMetadata` +
        `notFound()`, même structure que D.3. Réutilise `AgentCard` (D.2)
        pour la grille d'agents du créateur, pas de nouveau composant
        carte. **Ajout backend nécessaire avant de construire cette
        page** : `GET /api/creators/{id}/follow` n'existait pas — le
        `POST`/`DELETE` de l'Étape C ne permettaient pas de savoir si
        l'utilisateur courant suit déjà ce créateur ni d'afficher un
        compteur, impossible de faire un vrai bouton Follow sans ça.
        Ajouté : `api/auth.py` (`utilisateur_optionnel`, ne lève jamais,
        renvoie `None` si pas de token valide — pour les routes publiques
        mais personnalisables) et `api/creators.py`
        (`GET .../follow` → `{ total, suivi_par_moi }`). Vérifié par
        `ast.parse` (syntaxe Python valide) seulement — **pas de test
        `TestClient` ni de vraie base**, contrairement à ce que faisaient
        les sessions précédentes sur les nouveaux endpoints ; à tester
        avant de faire confiance à ce endpoint. Fichiers backend remis
        séparément (`djiguigne-backend-d4.zip`) — PAS ENCORE APPLIQUÉS
        sur `assistant-etudiants`, ni testés, ni poussés.
        `components/BoutonFollow.tsx` : toggle optimiste (met à jour le
        compteur localement après un POST/DELETE réussi, pas de
        rechargement depuis le serveur comme le fait `NoteAgent` — choix
        différent assumé, un follow/unfollow ne peut pas changer en
        parallèle par quelqu'un d'autre de manière pertinente à afficher
        immédiatement, contrairement à une moyenne de notes). Pas de
        bouton pour se suivre soi-même.
        Vérifié : `tsc --noEmit` sans erreur (après réinstallation des
        dépendances). `next build` non retesté à cette étape (limite
        Google Fonts déjà documentée à D.1/D.2/D.3, pas la peine de
        reconfirmer à chaque fois).
  - [x] **D.5 — Dashboard** (`/dashboard`) : Client Component (pas de
        SSR possible — la session Supabase vit dans le localStorage du
        navigateur, même contrainte que `/connexion`), redirige vers
        `/connexion` si pas de session. Deux sections : édition du profil
        (`PATCH /api/profiles/me`) et "Mes agents" — réutilise
        `GET /api/profiles/{user_id}` (même endpoint que le portfolio
        public D.4, appelé avec son propre `user_id`) plutôt qu'un
        endpoint dédié, 404 tolérée (compte tout juste inscrit, pas
        encore de ligne `profiles`) → formulaire vide, liste vide.
        **VOLONTAIREMENT PAS FAIT ICI : connexion aux outils externes
        (Notion) depuis le dashboard.** Le OAuth Notion
        (`demarrer_connexion_notion`/`finaliser_connexion_notion`,
        `connexions/notion.py`) n'existe qu'en Streamlit, rien n'est
        exposé par l'API FastAPI. Construire ce pont (endpoints API,
        gestion du callback OAuth, redirect URI probablement encore
        configuré vers l'app Streamlit en prod chez Notion) est un
        morceau à part, pas improvisé ici sans validation de Bourama. En
        attendant, la connexion Notion reste possible via le fallback
        déjà prévu dans le chat Streamlit (voir section "Compte unifié",
        "Chemin de connexion à un outil externe"). Prochaine IA : ne pas
        construire ce pont sans en discuter d'abord, un mauvais redirect
        URI casserait le flux Notion en prod.
        Vérifié : `tsc --noEmit` sans erreur.
  - [x] **D.6 — Créer un agent** (`/dashboard/agents/nouveau`) : formulaire
        construit champ par champ pour matcher EXACTEMENT
        `CreerAgentPayload` (`api/agents.py`) — mêmes chaînes exactes pour
        `ton` et `type_connaissance` que `faces/vues/creer_agent.py`
        (copiées, pas réinventées : `composer_system_prompt` interprète
        ces valeurs précises côté backend, un texte reformulé casserait
        silencieusement le prompt généré). Ordre : vitrine (nom, icône,
        image, description publique) → identité de base (ton, posture,
        limites) → comportements (4 lignes optionnelles) → base de
        connaissance (type, description, Notion, texte libre). Redirige
        vers `/agent/{id}` après création.
        **VOLONTAIREMENT PAS INCLUS :**
        - Sélection d'outils (`outils_choisis`) : toujours envoyé `[]`.
          La Streamlit lit `SERVEURS_MCP` dynamiquement
          (`core/registre_outils.py`), rien n'est exposé par l'API pour
          lister les outils côté frontend — nouvel endpoint
          `GET /api/outils` à faire plus tard si on veut cette
          fonctionnalité dans le nouveau flow.
        - Upload de PDF : `POST /api/agents` ne le gère pas lui-même
          (voir docstring du fichier, choix déjà fait avant le pivot
          social) — c'est `POST /api/agents/{id}/documents` (Étape 2 de
          `api/PLAN.md`), pas construit côté frontend ici. Un agent reste
          créable sans PDF (Notion et texte libre suffisent).
        Vérifié : `tsc --noEmit` sans erreur.
  **L'Étape D (frontend) est maintenant entièrement écrite (D.1 à D.6),
  aucune sous-étape restante dans la checklist.** Ne veut PAS dire
  "terminée" pour autant : voir la liste des manques ci-dessous et l'état
  de livraison plus bas. Prochaine étape possible : Étape F/G du plan
  original (déploiement, bascule Streamlit) — mais seulement après avoir
  comblé les manques connus, sans quoi on déploierait quelque chose de
  jamais testé.

  **Manques connus, listés pour ne rien oublier :**
  - `djiguigne-backend-d4.zip` (`GET /api/creators/{id}/follow`) jamais
    appliqué sur le repo ni testé
  - Connexion Notion depuis le dashboard : pas de pont API, décision à
    prendre avec Bourama avant de la construire (voir note D.5)
  - Sélection d'outils à la création d'un agent : pas d'endpoint pour
    lister les outils disponibles (voir note D.6)
  - Upload de PDF à la création d'un agent : endpoint existe
    (`POST /api/agents/{id}/documents`, Étape 2 de `api/PLAN.md`) mais
    pas de formulaire frontend pour l'appeler
  - `profiles.slug` toujours pas généré : les URLs `/u/[id]` utilisent
    `user_id` brut, pas un slug lisible
  - Point de vigilance iframe/X-Frame-Options (popup chat Streamlit,
    Étape D.3) jamais testé contre un vrai déploiement

**État de livraison D.1 à D.4 (important) : rien n'a été testé avec un
vrai `npm run dev`/build, ni poussé sur GitHub, à aucune étape.** Chaque
session a remis un zip cumulatif du dossier `frontend/` à Bourama, qui l'a
confirmé non testé et non poussé au 2026-07-11 (pour D.1/D.2, avant D.3).
`tsc --noEmit` passe à chaque fois, mais ça ne garantit ni un rendu
correct dans un vrai navigateur, ni que le popup iframe Streamlit
fonctionne, ni que les endpoints backend tout juste écrits (D.4)
fonctionnent contre une vraie base. Bourama a choisi de continuer sans
tester d'abord — son choix, mais ce risque s'accumule d'étape en étape,
noté ici pour que ce soit visible.
- 2026-07-11 — Étape D.3 (page agent `/agent/[id]`) écrite par-dessus le
  zip D.1/D.2 fourni par Bourama (confirmé : c'est bien un zip cumulatif,
  pas besoin d'un "zip D.1" séparé). Nouveau `lib/api-serveur.ts` pour le
  fetch SSR public, `app/agent/[id]/page.tsx` (Server Component,
  `generateMetadata`, `notFound()`), `components/BoutonUtiliser.tsx`
  (popup/plein écran Streamlit), `components/NoteAgent.tsx` (étoiles 1-5),
  `components/CommentairesAgent.tsx`. `NEXT_PUBLIC_STREAMLIT_URL` ajouté à
  `.env.local.example`. `tsc --noEmit` OK, `next build` toujours bloqué
  sur Google Fonts (réseau sandbox, pas un bug). **Rien de D.1/D.2/D.3
  n'est testé en conditions réelles ni poussé sur GitHub** — confirmé
  explicitement par Bourama pour D.1/D.2 avant de commencer D.3, noté en
  gros dans la section Étape D pour que ça ne s'accumule pas sans être vu.
- 2026-07-11 — Étape D.4 (portfolio créateur `/u/[id]`) : `app/u/[id]/page.tsx`
  (Server Component, réutilise `AgentCard`), `components/BoutonFollow.tsx`.
  Ajout backend requis et fait avant la page frontend : `GET
  /api/creators/{id}/follow` (`api/creators.py`) + `utilisateur_optionnel`
  (`api/auth.py`), remis dans `djiguigne-backend-d4.zip` séparé — PAS
  appliqué sur le repo, PAS testé avec `TestClient` (juste vérifié
  syntaxiquement avec `ast.parse`), PAS testé contre une vraie base.
  `tsc --noEmit` OK côté frontend. Bourama a demandé de continuer sans
  tester D.1-D.3 au préalable ; le risque d'accumulation est noté dans la
  section Étape D plutôt que de bloquer.
- 2026-07-11 — Étape D.5 (dashboard `/dashboard`) : édition de profil +
  "Mes agents" (réutilise `GET /api/profiles/{user_id}`, pas de nouvel
  endpoint). Connexion Notion depuis le dashboard volontairement PAS
  construite (le OAuth n'existe qu'en Streamlit, aucun pont API —
  décision à prendre avec Bourama avant de s'y attaquer, risque de casser
  le redirect URI en prod). `tsc --noEmit` OK. Toujours en attente :
  application de `djiguigne-backend-d4.zip` sur le repo.
- 2026-07-11 — Étape D.6 (créer un agent) : formulaire complet dans
  `app/dashboard/agents/nouveau/page.tsx`, payload aligné champ par champ
  sur `CreerAgentPayload`. Sélection d'outils et upload PDF volontairement
  hors scope (voir notes D.6). **L'Étape D du pivot social est maintenant
  entièrement écrite (D.1 à D.6)**, mais rien n'est testé en conditions
  réelles ni poussé sur GitHub à aucune sous-étape, et plusieurs manques
  connus restent listés dans la section Étape D (backend D.4 non
  appliqué, Notion dashboard, sélection d'outils, upload PDF, slug
  profils, vigilance iframe). Prochaine session : combler ces manques ou
  tester ce qui existe avant d'aller plus loin — au choix de Bourama.
- 2026-07-11 — Test en conditions réelles démarré par Bourama (`npm
  install` + `.env.local` configuré avec les secrets Streamlit Cloud).
  D.1 à D.6 tournent, testés comme un tout fonctionnel. Bourama corrige
  les bugs trouvés un par un au fil de l'eau plutôt que par étape —
  prochaine session : traiter les bugs remontés, pas continuer sur une
  nouvelle étape avant que ceux-ci soient réglés.
- 2026-07-12 — Bug corrigé (remonté par capture d'écran, page
  `/agent/math-matique` en prod sur Vercel — confirme au passage que D.3
  est bien déployé) : aucun bouton retour visible en ouvrant un agent.
  Ajout de `components/BoutonRetour.tsx` (`router.back()`, pas un lien
  fixe vers `/` — ramène là d'où on vient), posé sur `/agent/[id]` et
  `/u/[id]`. `tsc --noEmit` OK. Zip cumulatif complet redonné
  (`djiguigne-app-etape-d6-fix1.zip`).
- 2026-07-12 — Bug corrigé (remonté par 2 captures d'écran) : les champs
  "URL image de vitrine" et "URL avatar" demandaient de coller un lien à
  la main, pas utilisable pour du non-technique. Remplacés par un vrai
  upload de fichier. Ajouts :
  - Supabase : nouveau bucket public `images-publiques` (migration
    `bucket_images_publiques`), sans policy RLS (upload UNIQUEMENT via le
    backend avec la service role key, cohérent avec le reste du projet)
  - Backend : `api/uploads.py` (`POST /api/uploads/image`, multipart,
    jpeg/png/webp, 5 Mo max, chemin `{user_id}/{uuid}.{ext}`), enregistré
    dans `api/main.py`. Vérifié par `ast.parse` seulement, pas testé
    contre une vraie base (même limite que le fix D.4/follow)
  - Frontend : `lib/api.ts` a maintenant `appelerApiFichier` (variante
    multipart de `appelerApi`, pas de `Content-Type` manuel — le
    navigateur doit fixer le boundary lui-même) ; nouveau composant
    `components/ChampImage.tsx` (bouton + aperçu + upload immédiat au
    choix du fichier), branché sur D.6 (image de vitrine) et D.5 (avatar
    du dashboard), remplace les deux `<input type="text">` d'URL.
  `tsc --noEmit` OK. Deux zips séparés remis : frontend cumulatif complet
  (`djiguigne-app-etape-d6-fix2.zip`) et backend, seulement les 2 fichiers
  touchés (`djiguigne-backend-fix2.zip` — `api/uploads.py` nouveau,
  `api/main.py` modifié).
- 2026-07-12 — Bug corrigé (500 remonté par capture d'écran, PATCH
  /api/profiles/me) : `profiles.slug` est NOT NULL + UNIQUE en base (voir
  Étape B) mais rien ne le remplissait — le tout premier enregistrement
  de profil pour n'importe quel compte plantait systématiquement.
  `api/profiles.py` : génère le slug une seule fois, à la création de la
  ligne (jamais régénéré sur les PATCH suivants, pour ne pas casser un
  lien déjà partagé), avec repli déterministe en cas de collision
  (`{base}-{6 premiers caractères du user_id}`). Vérifié par `ast.parse`
  seulement (même limite que les fix précédents). Zip séparé
  (`djiguigne-backend-fix3.zip`, juste `api/profiles.py`).
- 2026-07-12 — Consolidation demandée par Bourama : les 3 patchs backend
  séparés (`djiguigne-backend-d4.zip`, `-fix2.zip`, `-fix3.zip`) fusionnés
  en un seul `djiguigne-backend-final.zip` (5 fichiers : `main.py`,
  `auth.py`, `uploads.py`, `profiles.py`, `creators.py`), pour un seul
  copier-coller au lieu de trois. Contenu identique, juste regroupé.
- 2026-07-12 — Bug corrigé (remonté par capture d'écran, formulaire de
  création d'agent) : aucun moyen d'ajouter un PDF, `POST /api/agents`
  ne le gère pas lui-même par choix initial (Étape 1 de `api/PLAN.md`),
  et l'Étape 2 (`POST /api/agents/{id}/documents`) n'avait jamais été
  construite. Fait maintenant :
  - Backend : nouveau `POST /api/agents/{agent_id}/documents` dans
    `api/agents.py` — réutilise `indexers/storage.py:upload_document` et
    `indexers/index_documents.py:indexer_document` tels quels (pas de
    duplication), vérifie la propriété de l'agent (403 sinon). Appelé
    APRÈS `POST /api/agents` (a besoin de l'id de l'agent déjà créé).
    Vérifié par `ast.parse` seulement.
  - Frontend : `app/dashboard/agents/nouveau/page.tsx` — champ fichier
    PDF ajouté, upload déclenché après la création de l'agent
    (best-effort : un échec n'empêche pas la redirection vers la page de
    l'agent, juste une alerte). `tsc --noEmit` OK.
  Zips finaux mis à jour (contenu cumulatif complet, mêmes noms de
  fichiers que d'habitude : `djiguigne-app-final.zip`,
  `djiguigne-backend-final.zip` — remplacent les précédents).
