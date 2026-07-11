# Plan de migration API — suivi séquentiel

But : remplacer progressivement les interfaces Streamlit (`faces/vues/*.py`)
par un backend FastAPI (ce dossier `api/`) appelé par un frontend Next.js
séparé (`app.djiguigne.com`, repo à part). Ce fichier est la source de
vérité de l'avancement — à lire en entier avant de continuer le travail,
que ce soit une autre session d'IA ou une personne.

**Règle pour quiconque reprend ce travail** : après chaque étape terminée,
cocher la case ici, ajouter une ligne dans le Changelog en bas avec la
date, et ne JAMAIS laisser une étape à moitié faite sans une note claire
dans "État exact" expliquant précisément ce qui manque.

---

## Décisions d'architecture déjà prises (ne pas remettre en question sans en discuter avec Bourama)

1. **Auth** : Next.js parle DIRECTEMENT à Supabase Auth via son SDK JS
   (inscription/connexion côté frontend). Le frontend envoie ensuite le
   `access_token` Supabase dans l'en-tête `Authorization: Bearer ...` à
   chaque appel API. Le backend FastAPI ne gère jamais de mot de passe,
   il vérifie juste le token via `supabase.auth.get_user(token)`.
2. **Emplacement** : ce backend vit dans CE MÊME dépôt (`assistant-etudiants`),
   dossier `api/`, pas un repo séparé. Déployé sur Railway (déjà le service
   utilisé pour ce dépôt).
3. **Réutilisation du code existant** : ce dossier ne duplique JAMAIS la
   logique métier. Il importe et appelle `core/*.py` et `indexers/*.py`
   tels quels. Si une fonction de `core/`/`indexers/` n'est pas assez
   générique pour être appelée depuis l'API (ex: elle lit directement
   `st.secrets`), on l'adapte sur PLACE dans son fichier d'origine, on ne
   la recopie pas dans `api/`.
4. **CORS** : whitelist du domaine du futur frontend (`app.djiguigne.com`
   + `localhost:3000` pour le dev local).
5. **Repo frontend séparé** : `app.djiguigne.com` (Next.js), distinct du
   site vitrine `djiguigne-frontend`. Pas encore créé au moment d'écrire
   ce fichier.

---

## Étapes séquentielles

### Étape 0 — Squelette FastAPI
- [x] `api/main.py` : app FastAPI, CORS, `GET /health`
- [x] `api/auth.py` : fonction `utilisateur_courant(token)` qui vérifie le
      JWT Supabase et retourne l'utilisateur (ou lève une 401)
- [x] `api/requirements.txt` (fastapi, uvicorn, python-multipart pour
      l'upload de fichiers, supabase)
- [x] Vérifié : `uvicorn api.main:app` démarre sans erreur en local,
      `GET /health` → 200 `{"status":"ok"}`, `GET /health/me` sans token
      → 401 `{"detail":"Token d'authentification manquant"}` (testé pour
      de vrai en local, pas juste relu — voir Changelog)

**État exact : FAIT et vérifié en local.** Pas encore testé avec un VRAI
token Supabase valide (nécessite une vraie session utilisateur créée
depuis un frontend ou un script ; le cas négatif — token absent — est
lui bien vérifié). Pas encore déployé sur Railway : ce dossier `api/`
existe dans le repo mais Railway continue de faire tourner Streamlit
pour l'instant (voir décision #2 : la bascule réelle du service Railway
vers cette API se fait à l'Étape 6, pas avant).

### Étape 1 — Création d'agent (`POST /api/agents`)
- [ ] Reprend la logique de `faces/vues/creer_agent.py` (construction de
      `system_prompt`, `ui_config`, `tools_enabled`, insertion avec
      `owner_id`), mais en payload JSON au lieu d'un formulaire Streamlit
- [ ] Validation : `agent_id` déjà pris → 409, pas 500
- [ ] Retourne l'agent créé + le lien (`URL_RETOUR_APP` + `?agent=...`)

**État exact : PAS COMMENCÉ.**

### Étape 2 — Upload de documents (`POST /api/agents/{id}/documents`, `POST /api/agents/{id}/texte`)
- [ ] Vérifie que `owner_id` du token correspond au propriétaire de l'agent
      (403 sinon)
- [ ] Appelle `indexers/index_documents.py:indexer_document`/`indexer_texte`
      tels quels

**État exact : PAS COMMENCÉ.**

### Étape 3 — Dashboard (`GET /api/agents`, `PATCH /api/agents/{id}`, `DELETE /api/agents/{id}/documents/{nom}`)
Équivalent de `faces/vues/mes_agents.py` : lister ses agents, activer/
désactiver, lister/ouvrir/supprimer les documents indexés.

**État exact : PAS COMMENCÉ.**

### Étape 4 — Chat (`POST /api/chat`, streaming SSE)
La plus sensible : `core/main.py:chat()` est un générateur Python, à
transformer en flux SSE (`text/event-stream`) exploitable par Next.js.
Gérer aussi le flux `confirmation_requise` (outils sensibles) en HTTP,
pas juste en mémoire de session Streamlit.

**État exact : PAS COMMENCÉ.**

### Étape 5 — Frontend Next.js (`app.djiguigne.com`)
Nouveau repo. Pages : connexion/inscription (Supabase JS direct), créer
un agent, mes agents, chat. Consomme l'API construite aux étapes 0-4.

**État exact : PAS COMMENCÉ. Repo pas encore créé.**

### Étape 6 — Bascule finale
Une fois les étapes 0-5 validées en conditions réelles (pas juste en
théorie — cf. la leçon du chantier Supabase précédent) : décommissionner
les déploiements Streamlit (Cloud et/ou Railway), retirer `faces/` et les
dépendances Streamlit de `requirements.txt`.

**État exact : PAS COMMENCÉ.**

---

## Changelog

- 2026-07-11 — Étape 0 terminée : squelette FastAPI créé (`api/main.py`,
  `api/auth.py`, `api/requirements.txt`, `api/__init__.py`). Testé en
  local : serveur démarre, `/health` → 200, `/health/me` sans token →
  401. Prochaine étape à faire : Étape 1 (`POST /api/agents`).
