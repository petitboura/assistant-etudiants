import os
from dotenv import load_dotenv
import requests
from configuration import get_system_prompt
from router import router
from retriever import recuperer_ressources, get_liste_prompts
from web import recherche_web
import time

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def get_ressources_disponibles():
    prompts = [p["nom"] for p in get_liste_prompts()]
    pdfs = []  # à compléter plus tard avec liste Supabase
    outils = ["tavily"]
    return prompts, pdfs, outils

def chat(message_utilisateur):
    system_prompt = get_system_prompt()

    # Étape 1 — Router décide
    prompts_dispo, pdfs_dispo, outils_dispo = get_ressources_disponibles()
    decision = router(message_utilisateur, prompts_dispo, pdfs_dispo, outils_dispo)

    # Étape 2 — Récupérer les ressources choisies
    contexte = recuperer_ressources(decision)

    # Étape 3 — Recherche web si nécessaire
    if decision.get("outil") == "tavily":
        resultats_web = recherche_web(message_utilisateur)
        contexte += f"\n--- Recherche web ---\n{resultats_web}"

    # Étape 4 — Assembler le prompt final
    if contexte:
        message_final = f"""Voici les ressources pertinentes :

{contexte}

Question de l'étudiant : {message_utilisateur}"""
    else:
        message_final = message_utilisateur

    # Étape 5 — Grand LLM répond
    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "meta-llama/llama-3.3-70b-instruct",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message_final}
            ]
        }
    )

    return response.json()["choices"][0]["message"]["content"]