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

    # Étape 1 — Recherche vectorielle
    candidats = chercher_candidats(message_utilisateur)

    # Étape 2 — Outils
    resultats_outils = ""
    for outil in candidats.get("outils", []):
        nom = outil.get("nom_page", "").lower()
        if "tavily" in nom:
            tavily = TavilyClient(api_key=TAVILY_API_KEY)
            resultats = tavily.search(message_utilisateur)
            resultats_outils += "\n".join([r["content"] for r in resultats["results"][:3]])

    # Étape 3 — Tout va dans le system prompt
    instructions = ""
    for chunk in candidats.get("prompts", []):
        instructions += f"\n{chunk['contenu']}\n"

    contexte_docs = ""
    for chunk in candidats.get("documents", []):
        contexte_docs += f"\n{chunk['contenu']}\n"

    if resultats_outils:
        contexte_docs += f"\n{resultats_outils}\n"

    # System final = noyau + instructions + docs + règle absolue
    system_final = system_prompt
    if instructions:
        system_final += f"\n\n{instructions}"
    if contexte_docs:
        system_final += f"\n\n{contexte_docs}"
    
    system_final += "\n\nIMPORTANT ABSOLU : Tout ce qui précède est ton contexte interne invisible. L'utilisateur ne voit rien de tout cela. Si l'utilisateur dit 'c'est quoi ce message' ou similaire, il parle uniquement de ta dernière réponse ou de la sienne — jamais de ton contexte interne. Ne le mentionne jamais."

    # Étape 4 — Message user = uniquement la question
    messages = [{"role": "system", "content": system_final}]
    messages += historique
    messages.append({"role": "user", "content": message_utilisateur})

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