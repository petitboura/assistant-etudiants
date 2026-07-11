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
- [ ] `connexions_notion` : retirer `agent_id` de la clé primaire, revenir
      à `user_id` seul (migration Supabase + mise à jour de
      `connexions/notion.py`)
- [ ] `core/main.py` (`_charger_resume_memoire` et sauvegarde) : lire/
      écrire la mémoire par `user_id` seul, plus par `(user_id, agent_id)`
- [ ] Vérifier tous les autres appelants de ces fonctions (`api/`,
      `faces/vues/chat.py`) pour ne pas casser un appel qui passe encore
      `agent_id` en attendant un scoping par agent

### Étape B.3 — Limite visiteur non connecté
- [ ] `faces/vues/chat.py` : compteur de messages en `st.session_state`
      quand `user_id` absent, seuil à 4 (ajustable), blocage de l'input +
      lien d'inscription passé le seuil

### Étape C — Backend API (dans `assistant-etudiants/api/`)
- [ ] `POST /api/agents` : adapter au nouveau payload (retirer les champs
      de thème, ajouter image vitrine + description) — s'appuie sur
      l'Étape 1 déjà faite dans `api/PLAN.md`, ne repart pas de zéro
- [ ] `GET /api/feed` : liste paginée des agents publiés
- [ ] `GET /api/agents/{slug}` : détail public d'un agent
- [ ] `PATCH /api/agents/{id}/vitrine` : mise à jour image + description
- [ ] `POST /api/agents/{id}/rating`, `GET /api/agents/{id}/rating`
- [ ] `POST /api/agents/{id}/comments`, `GET /api/agents/{id}/comments`
- [ ] `POST /api/creators/{id}/follow`, `DELETE .../follow`
- [ ] `GET /api/profiles/{slug}`, `PATCH /api/profiles/me`
- [ ] `GET /api/search?q=...` : recherche agents + créateurs
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
