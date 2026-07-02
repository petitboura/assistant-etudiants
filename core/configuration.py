"""
Charge et met en cache le system prompt central depuis Notion.
Rechargement automatique toutes les 5 minutes.
"""

import os
import time
import logging
import requests


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


NOTION_TOKEN = get_secret("NOTION_TOKEN")
NOTION_PAGE_ID = get_secret("NOTION_PAGE_ID")

# Cache en mémoire
_cache = {
    "prompt": None,
    "timestamp": 0
}

CACHE_DUREE = 300  # 5 minutes


def _cache_expire():
    # timestamp == 0 -> jamais chargé, donc considéré comme expiré
    return _cache["timestamp"] == 0 or time.time() - _cache["timestamp"] > CACHE_DUREE


def _charger_depuis_notion():
    if not NOTION_TOKEN or not NOTION_PAGE_ID:
        logging.error(
            "NOTION_TOKEN ou NOTION_PAGE_ID manquant (vérifie tes secrets/variables d'environnement)."
        )
        return

    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children?page_size=100"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28"
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception as e:
        logging.error(f"ERREUR NOTION (requête impossible) : {e}")
        return

    if response.status_code != 200:
        # Cas classique : token invalide (401) ou page non partagée avec l'intégration (404)
        logging.error(
            f"ERREUR NOTION {response.status_code} : {response.text[:500]}"
        )
        return

    data = response.json()
    blocks = data.get("results", [])

    texte = ""
    for block in blocks:
        type_block = block.get("type")
        if type_block in ["paragraph", "bulleted_list_item", "numbered_list_item",
                           "heading_1", "heading_2", "heading_3"]:
            rich_text = block.get(type_block, {}).get("rich_text", [])
            for t in rich_text:
                texte += t.get("plain_text", "") + "\n"

    texte = texte.strip()
    if not texte:
        logging.warning(
            "Le prompt Notion récupéré est VIDE. La page existe et répond (200 OK) "
            "mais ne contient aucun bloc paragraph/liste/heading exploitable "
            "(peut-être du contenu dans des toggles, colonnes, ou sous-pages non gérées ici)."
        )

    _cache["prompt"] = texte
    _cache["timestamp"] = time.time()


def get_system_prompt():
    if _cache_expire():
        _charger_depuis_notion()
    return _cache["prompt"]


def forcer_rechargement():
    _cache["timestamp"] = 0
    return get_system_prompt()
