"""
Script d'indexation périodique du contenu Notion (page "Mes prompts" et ses sous-pages)
vers la table prompts_chunks dans Supabase.

Ne tourne PAS à chaque question d'étudiant — lancé périodiquement (GitHub Action)
ou manuellement pour tester.

Logique de mise à jour incrémentale :
- Pour chaque page rencontrée, on compare son last_edited_time à ce qui est stocké
- Si identique -> on ne touche à rien (économie d'appels API d'embedding)
- Si différent ou absent -> on supprime les anciens chunks de CETTE page, puis on réindexe
"""

import os
from dotenv import load_dotenv
import requests
from supabase import create_client
import openai

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28"
}


def get_page_metadata(page_id):
    """Récupère le titre et la date de dernière modification d'une page Notion."""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    response = requests.get(url, headers=HEADERS)
    data = response.json()
    last_edited = data.get("last_edited_time")

    titre = "Sans titre"
    proprietes = data.get("properties", {})
    for prop in proprietes.values():
        if prop.get("type") == "title":
            morceaux = prop.get("title", [])
            if morceaux:
                titre = morceaux[0].get("plain_text", "Sans titre")

    return titre, last_edited


def get_texte_et_sous_pages(block_id):
    """
    Parcourt les blocs d'une page. Retourne (texte_de_la_page, liste_des_ids_sous_pages).
    Gère les pages classiques ET les bases de données (chaque ligne = une sous-page).
    """
    url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
    response = requests.get(url, headers=HEADERS)
    blocks = response.json().get("results", [])

    texte = ""
    sous_pages = []

    for block in blocks:
        type_block = block.get("type")

        # Texte simple (paragraphe, listes...)
        if type_block in ["paragraph", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"]:
            rich_text = block[type_block].get("rich_text", [])
            for t in rich_text:
                texte += t.get("plain_text", "") + "\n"

        # Sous-page classique
        elif type_block == "child_page":
            sous_pages.append(block["id"])

        # Base de données -> chaque ligne est traitée comme une sous-page
        elif type_block == "child_database":
            sous_pages.extend(get_lignes_database(block["id"]))

    return texte.strip(), sous_pages


def get_lignes_database(database_id):
    """Retourne les IDs de chaque ligne (entrée) d'une base de données Notion."""
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    response = requests.post(url, headers=HEADERS, json={"page_size": 100})
    resultats = response.json().get("results", [])
    return [r["id"] for r in resultats]


def decouper_texte(texte, taille=500):
    mots = texte.split()
    return [" ".join(mots[i:i + taille]) for i in range(0, len(mots), taille)] or [""]


def creer_embedding(texte):
    response = client.embeddings.create(model="text-embedding-ada-002", input=texte)
    return response.data[0].embedding


def get_last_edited_stocke(page_id):
    """Vérifie si cette page est déjà indexée, et avec quelle date."""
    result = supabase.table("prompts_chunks").select("last_edited_time").eq("page_id", page_id).limit(1).execute()
    if result.data:
        return result.data[0]["last_edited_time"]
    return None


def get_table(nom_page):
    """Choisit la table Supabase selon le nom de la page."""
    if "outil" in nom_page.lower():
        return "outils_chunks"
    return "prompts_chunks"

def indexer_page(page_id, nom_page, last_edited_time):
    """Supprime les anciens chunks de cette page, découpe + vectorise + insère les nouveaux."""
    table = get_table(nom_page)
    supabase.table(table).delete().eq("page_id", page_id).execute()

    texte, _ = get_texte_et_sous_pages(page_id)
    if not texte:
        print(f"  -> '{nom_page}' est vide, rien à indexer.")
        return

    morceaux = decouper_texte(texte)
    for morceau in morceaux:
        embedding = creer_embedding(morceau)
        supabase.table(table).insert({
            "page_id": page_id,
            "nom_page": nom_page,
            "contenu": morceau,
            "embedding": embedding,
            "last_edited_time": last_edited_time
        }).execute()

    print(f"  -> '{nom_page}' indexée dans '{table}' ({len(morceaux)} morceaux).")


def parcourir_et_indexer(page_id, profondeur=0):
    """Parcourt récursivement une page et ses sous-pages, à n'importe quelle profondeur."""
    nom_page, last_edited_actuel = get_page_metadata(page_id)
    prefixe = "  " * profondeur

    last_edited_stocke = get_last_edited_stocke(page_id)

    if last_edited_stocke == last_edited_actuel:
        print(f"{prefixe}'{nom_page}' inchangée, ignorée.")
    else:
        print(f"{prefixe}'{nom_page}' modifiée ou nouvelle, indexation...")
        indexer_page(page_id, nom_page, last_edited_actuel)

    # Récursion sur les sous-pages, peu importe si la page elle-même a changé ou non
    _, sous_pages = get_texte_et_sous_pages(page_id)
    for sous_page_id in sous_pages:
        parcourir_et_indexer(sous_page_id, profondeur + 1)


if __name__ == "__main__":
    print("Démarrage de l'indexation Notion -> Supabase...")
    parcourir_et_indexer(NOTION_PAGE_ID)
    print("Terminé.")