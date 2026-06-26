import os
import requests
from dotenv import load_dotenv
from configuration import get_system_prompt
from retriever import chercher_candidats

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def chat(message_utilisateur, historique=[]):
    system_prompt = get_system_prompt()

    # Étape 1 — Recherche vectorielle dans toutes les tables
    candidats = chercher_candidats(message_utilisateur)

    # Étape 2 — Assembler le contexte
    contexte = ""

    for chunk in candidats.get("prompts", []):
        contexte += f"\n--- Instruction ---\n{chunk['contenu']}\n"

    for chunk in candidats.get("documents", []):
        contexte += f"\n--- Document ---\n{chunk['contenu']}\n"

    for chunk in candidats.get("outils", []):
        contexte += f"\n--- Outil disponible ---\n{chunk['contenu']}\n"

    # Étape 3 — Assembler le message final
    if contexte:
        message_final = f"Ressources pertinentes :\n{contexte}\n\nQuestion : {message_utilisateur}"
    else:
        message_final = message_utilisateur

    # Étape 4 — Historique + grand LLM
    messages = [{"role": "system", "content": system_prompt}]
    messages += historique
    messages.append({"role": "user", "content": message_final})

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "meta-llama/llama-3.3-70b-instruct",
            "messages": messages
        }
    )

    return response.json()["choices"][0]["message"]["content"]