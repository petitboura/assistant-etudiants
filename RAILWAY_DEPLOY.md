# Déploiement Railway — checklist

Cette liste est établie en lisant le code réel (grep de `get_secret("...")` sur
tout le dépôt), pas le README (obsolète sur ce point). À reporter dans Railway
→ ton projet → Variables, avec les mêmes valeurs que dans les secrets
Streamlit Cloud actuels (`.streamlit/secrets.toml` ou l'interface Streamlit
Cloud → Settings → Secrets).

## Variables à reporter telles quelles

| Variable | Utilisée par |
|---|---|
| `SUPABASE_URL` | tout le projet |
| `SUPABASE_SECRET` | tout le projet |
| `GROQ_API_KEY` | core/main.py (LLM principal + secours) |
| `GOOGLE_API_KEY` | core/embeddings.py, core/main.py (secours Gemini) |
| `NOTION_TOKEN` | indexers/index_notion.py, connexions/notion.py |
| `TAVILY_API_KEY` | core/registre_outils.py (outil de recherche web) |

## Variable à RECALCULER (pas à copier telle quelle)

| Variable | Pourquoi |
|---|---|
| `URL_RETOUR_APP` | URL de retour pour la connexion OAuth Notion (connexions/notion.py). Doit correspondre à l'URL publique réelle du déploiement. Elle changera une 2e fois quand le sous-domaine par agent (`son-agent.djiguigne.com`) sera branché — prévoir de la remettre à jour à ce moment-là aussi. |

## Variable à NE PAS reporter

| Variable | Pourquoi |
|---|---|
| `AGENT_ID` | Existait comme secret de repli avant l'Étape 1 (agent unique codé en dur). Aujourd'hui `faces/app_etudiant.py` lit l'agent depuis `?agent=` dans l'URL en priorité — la variable ne sert plus qu'en tout dernier recours si jamais aucun paramètre d'URL n'est présent. Optionnel, à ne remettre que si tu veux un agent par défaut précis en cas d'URL sans `?agent=`.

## Variables obsolètes (ignorées par le code actuel malgré le README)

Ne pas chercher à les reporter, elles ne sont lues nulle part dans le code
actuel :
- `OPENROUTER_API_KEY` (remplacé par `GOOGLE_API_KEY`, migration Gemini)
- `NOTION_PAGE_ID` (remplacé par la colonne `agents.notion_page_id`, multi-agent)

## Fichiers ajoutés pour Railway

- `Procfile` — commande de démarrage (`streamlit run faces/app_etudiant.py`,
  lié au port fourni par Railway via `$PORT`)

## Après le premier déploiement Railway

1. Railway donne un domaine temporaire (`xxx.up.railway.app`) — vérifier que
   l'app démarre et répond avant de toucher au DNS.
2. Mettre à jour `URL_RETOUR_APP` avec ce domaine temporaire, tester la
   connexion Notion (bouton "Connecter Notion" côté utilisateur).
3. Une fois `djiguigne.com` acheté chez Porkbun : configurer le domaine
   personnalisé dans Railway, un enregistrement wildcard (`*.djiguigne.com`)
   sera nécessaire pour l'Étape 5 (sous-domaine par agent) — ce point reste à
   coder séparément (lecture de l'agent depuis le sous-domaine de la requête
   plutôt que depuis `?agent=`), une fois le domaine réellement actif.
