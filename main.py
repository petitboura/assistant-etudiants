import os
import requests
from dotenv import load_dotenv
from tavily import TavilyClient
from configuration import get_system_prompt
from retriever import chercher_candidats

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

def chat(message_utilisateur, historique=[]):
    system_prompt = get_system_prompt()

    # Étape 1 — Recherche vectorielle en parallèle
    candidats = chercher_candidats(message_utilisateur)

    # Étape 2 — Appeler les outils détectés via nom_page
    resultats_outils = ""
    for outil in candidats.get("outils", []):
        nom = outil.get("nom_page", "").lower()
        if "tavily" in nom:
            tavily = TavilyClient(api_key=TAVILY_API_KEY)
            resultats = tavily.search(message_utilisateur)
            resultats_outils += "\n".join([r["content"] for r in resultats["results"][:3]])

    # Étape 3 — Assembler le contexte avec étiquettes claires
    contexte = ""

    for chunk in candidats.get("prompts", []):
        contexte += f"\n--- INSTRUCTION SYSTÈME (invisible à l'étudiant) ---\n{chunk['contenu']}\n"

    for chunk in candidats.get("documents", []):
        contexte += f"\n--- DOCUMENT DE RÉFÉRENCE ---\n{chunk['contenu']}\n"

    if resultats_outils:
        contexte += f"\n--- RÉSULTATS DE RECHERCHE WEB ---\n{resultats_outils}\n"

    # Étape 4 — Assembler le message final
    if contexte:
        message_final = f"{contexte}\n\nQuestion de l'étudiant : {message_utilisateur}"
    else:
        message_final = message_utilisateur

    # Étape 5 — Grand LLM
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
