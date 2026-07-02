# assistant-etudiants — version restructurée

Ce projet a été réorganisé sur le modèle de **telecom-ia (Ooredoo)**, en gardant
toutes les fonctionnalités propres à assistant-etudiants (RAG PDF + outil Tavily).

## Structure

```
core/
  main.py           chat() — cascade Groq → Gemini → Groq de secours + assemblage du prompt + Tavily
  configuration.py  system prompt central chargé depuis Notion (cache 5 min)
  retriever.py      recherche vectorielle parallèle (prompts / documents / outils)
  embeddings.py     vectoriser() et decouper_texte() partagés (avant dupliqués 2x)

faces/
  app_etudiant.py   interface Streamlit (coach mathématique)

indexers/
  index_notion.py     indexation récursive Notion -> Supabase (prompts_chunks / outils_chunks)
  index_documents.py  indexation PDF -> Supabase (table documents), pour le RAG documentaire
  storage.py          upload / liste / suppression de documents dans Supabase Storage
```

## Ce qui a changé par rapport à l'original

- **Structure en dossiers** (`core/`, `faces/`, `indexers/`) au lieu de tout à la racine
- **`get_secret()` cohérent partout** — fonctionne aussi bien en local (`.env`) qu'en
  déploiement Streamlit (`st.secrets`)
- **Bug corrigé** : `IA pour etudiants.py` utilisait `SUPABASE_KEY` alors que tout le
  reste du projet utilise `SUPABASE_SECRET` — unifié
- **Cascade de fallback multi-modèles** : avant, un seul appel à Llama 3.3 via
  OpenRouter sans filet. Maintenant : Groq (gpt-oss-120b) → Gemini 2.5 Flash →
  3 modèles Groq de secours, comme sur Ooredoo
- **Fin de la duplication de code** : `decouper_texte()` et la création d'embedding
  étaient réécrites à l'identique dans `rag.py` et `indexer_notion.py` → un seul
  module `core/embeddings.py`
- **`requirements.txt` nettoyé** : dédupliqué, sans les artefacts Windows (`\r`),
  avec les nouvelles dépendances (`groq`, `google-genai`)

## Ce qui a été conservé (fonctionnalités absentes chez Ooredoo)

- **RAG documentaire (PDF)** : `indexers/index_documents.py` + table `documents`
- **Outil de recherche web Tavily** : déclenché dynamiquement dans `core/main.py`
  quand la recherche vectorielle remonte un outil dont le nom contient "tavily"
- **Indexation récursive Notion** avec gestion des bases de données et répartition
  automatique en deux tables selon le nom de la page

## Variables d'environnement / secrets nécessaires

```
GROQ_API_KEY
GOOGLE_API_KEY
OPENROUTER_API_KEY   (pour les embeddings)
TAVILY_API_KEY
NOTION_TOKEN
NOTION_PAGE_ID
SUPABASE_URL
SUPABASE_SECRET
```

## Lancer l'app

```
streamlit run faces/app_etudiant.py
```

## Indexer un nouveau document PDF

```
python indexers/index_documents.py mon_document.pdf
```
(le fichier doit déjà être présent dans le bucket Supabase Storage "IA pour etudiants")

## Ce que je n'ai PAS touché

- La table `documents` reste vectorisée avec `text-embedding-ada-002` (via OpenRouter),
  pas migrée vers l'embedding Gemini utilisé par Ooredoo — changer de modèle
  d'embedding changerait la dimension des vecteurs et casserait la recherche
  existante sans réindexation complète. À voir si tu veux migrer plus tard.
- La logique métier (limite de 5 messages, formulaire de feedback) — inchangée,
  ce n'est pas un problème de qualité de code, juste un choix produit.
