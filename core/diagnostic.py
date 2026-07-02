"""
Script de diagnostic — teste chaque maillon de la chaîne indépendamment
et affiche clairement où ça casse.

Lancement :
    cd core
    python ../diagnostic.py

(les variables d'environnement / st.secrets doivent être disponibles,
donc lance-le depuis un environnement où GROQ_API_KEY, NOTION_TOKEN,
NOTION_PAGE_ID, SUPABASE_URL, SUPABASE_SECRET, OPENROUTER_API_KEY, etc.
sont déjà exportées, comme pour l'app elle-même)
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), "core"))

OK = "✅"
KO = "❌"
WARN = "⚠️ "


def check_env_vars():
    print("\n=== 1. Variables d'environnement ===")
    requis = [
        "NOTION_TOKEN", "NOTION_PAGE_ID",
        "SUPABASE_URL", "SUPABASE_SECRET",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ]
    manquants = []
    for var in requis:
        val = os.environ.get(var)
        if val:
            print(f"{OK} {var} défini ({len(val)} caractères)")
        else:
            print(f"{KO} {var} ABSENT")
            manquants.append(var)
    return manquants


def check_notion():
    print("\n=== 2. Notion (system prompt) ===")
    import requests
    token = os.environ.get("NOTION_TOKEN")
    page_id = os.environ.get("NOTION_PAGE_ID")
    if not token or not page_id:
        print(f"{KO} NOTION_TOKEN ou NOTION_PAGE_ID absent, test impossible.")
        return

    url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        print(f"{KO} Impossible de contacter l'API Notion : {e}")
        return

    if r.status_code == 401:
        print(f"{KO} 401 Unauthorized -> NOTION_TOKEN invalide ou expiré.")
        return
    if r.status_code == 404:
        print(f"{KO} 404 Not Found -> la page n'existe pas OU (cas le plus fréquent) "
              f"l'intégration Notion n'a PAS été partagée avec cette page. "
              f"Va sur la page Notion -> ... -> Connexions -> ajoute ton intégration.")
        return
    if r.status_code != 200:
        print(f"{KO} Statut HTTP {r.status_code} : {r.text[:300]}")
        return

    blocks = r.json().get("results", [])
    print(f"{OK} 200 OK, {len(blocks)} blocs enfants trouvés.")

    texte = ""
    types_rencontres = {}
    for b in blocks:
        t = b.get("type")
        types_rencontres[t] = types_rencontres.get(t, 0) + 1
        if t in ["paragraph", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"]:
            for rt in b.get(t, {}).get("rich_text", []):
                texte += rt.get("plain_text", "")

    print(f"    Types de blocs rencontrés : {types_rencontres}")
    if texte.strip():
        print(f"{OK} Texte extrait : {len(texte)} caractères. Aperçu : {texte[:120]!r}")
    else:
        print(f"{WARN} Aucun texte exploitable extrait. Si tu vois des types comme "
              f"'toggle', 'column_list', 'table' ci-dessus : le code actuel ne les gère pas, "
              f"il faut les ajouter dans configuration.py.")


def check_openrouter_embeddings():
    print("\n=== 3. OpenRouter (embeddings) ===")
    try:
        from embeddings import vectoriser
    except Exception as e:
        print(f"{KO} Impossible d'importer embeddings.py : {e}")
        return None
    try:
        vecteur = vectoriser("test de vectorisation")
        print(f"{OK} Embedding généré, dimension = {len(vecteur)}")
        return vecteur
    except Exception as e:
        print(f"{KO} ERREUR lors de la vectorisation : {e}")
        print(f"    -> Vérifie OPENROUTER_API_KEY, et que le modèle "
              f"'openai/text-embedding-ada-002' est bien accessible via OpenRouter "
              f"avec ta clé (certains modèles d'embedding nécessitent des credits "
              f"ou ne sont pas activés par défaut).")
        return None


def check_supabase(vecteur):
    print("\n=== 4. Supabase (tables + fonctions RPC) ===")
    try:
        from supabase import create_client
    except Exception as e:
        print(f"{KO} Impossible d'importer supabase : {e}")
        return

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET")
    if not url or not key:
        print(f"{KO} SUPABASE_URL ou SUPABASE_SECRET absent, test impossible.")
        return

    client = create_client(url, key)

    # Vérifie que les tables contiennent des données du tout
    for table in ["prompts_chunks", "documents", "outils_chunks"]:
        try:
            res = client.table(table).select("*", count="exact").limit(1).execute()
            print(f"{OK} Table '{table}' accessible, {res.count} ligne(s) au total.")
            if res.count == 0:
                print(f"{WARN} La table '{table}' est VIDE -> les indexeurs "
                      f"(index_notion.py / index_documents.py) n'ont probablement jamais tourné avec succès.")
        except Exception as e:
            print(f"{KO} Erreur sur la table '{table}' : {e}")

    if vecteur is None:
        print(f"{WARN} Pas de vecteur disponible (échec étape 3), on ne peut pas tester les fonctions RPC.")
        return

    for fn, match_count in [("recherche_prompts", 3), ("recherche_documents", 3), ("recherche_outils", 2)]:
        try:
            res = client.rpc(fn, {"query_embedding": vecteur, "match_count": match_count}).execute()
            n = len(res.data or [])
            print(f"{OK} RPC '{fn}' exécutée, {n} résultat(s).")
            if n == 0:
                print(f"{WARN} 0 résultat -> soit la table liée est vide, soit le seuil de similarité "
                      f"dans la fonction SQL est trop strict.")
        except Exception as e:
            print(f"{KO} Erreur RPC '{fn}' : {e}")
            print(f"    -> Vérifie que la fonction existe bien dans Supabase (SQL Editor) "
                  f"et que sa signature correspond (query_embedding, match_count).")


def main():
    print("Diagnostic assistant-etudiants")
    print("=" * 40)
    manquants = check_env_vars()
    check_notion()
    vecteur = check_openrouter_embeddings()
    check_supabase(vecteur)

    print("\n=== Résumé ===")
    if manquants:
        print(f"{KO} Variables manquantes : {', '.join(manquants)}")
    print("Relis les sections ci-dessus marquées ❌ ou ⚠️  : ce sont les points de blocage réels.")


if __name__ == "__main__":
    main()
