"""
Charge et met en cache le system prompt central depuis Notion.
Rechargement automatique toutes les 5 minutes.
"""

import os
import time
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
    url = f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28"
    }
    response = requests.get(url, headers=headers)
    blocks = response.json().get("results", [])

    texte = ""
    for block in blocks:
        type_block = block.get("type")
        if type_block in ["paragraph", "bulleted_list_item", "numbered_list_item"]:
            rich_text = block[type_block].get("rich_text", [])
            for t in rich_text:
                texte += t.get("plain_text", "") + "\n"

    _cache["prompt"] = texte.strip()
    _cache["timestamp"] = time.time()


def get_system_prompt():
    if _cache_expire():
        _charger_depuis_notion()
    return _cache["prompt"]


def forcer_rechargement():
    _cache["timestamp"] = 0
    return get_system_prompt()
