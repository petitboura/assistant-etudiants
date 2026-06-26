import os
import requests
from dotenv import load_dotenv
from supabase import create_client
from configuration import get_system_prompt
from retriever import chercher_candidats
from router import router

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def appeler_outil(nom_outil, question):
    outil = supabase.table("outils").select("*").eq("nom", nom_outil).single().execute().data
    if not outil:
        return ""
    
    if outil["type"] == "api":
        config = outil["config"]
        cle = os.getenv(config["env_key"])
        response = requests.post(
            config["url"],
            json={config["query_param"]: question, config["key_param"]: cle}
        )
        resultats = response.json().get(config["results_key"], [])
        return "\n".join([r.get(config["content_key"], "") for r in resultats])
    
    return ""

def chat(message_utilisateur, historique=[]):
    system_prompt = get_system_prompt()

    # Étape 1 — Recherche vectorielle dans toutes les tables
    candidats = chercher_candidats(message_utilisateur)

    # Étape 2 — Petit LLM décide quoi garder
    decision = router(message_utilisateur, candidats)

    # Étape 3 — Assembler le contexte
    contexte = ""

    for contenu in decision.get("prompts", []):
        contexte += f"\n--- Instruction ---\n{contenu}\n"

    for contenu in decision.get("documents", []):
        contexte += f"\n--- Document ---\n{contenu}\n"

    for nom_outil in decision.get("outils", []):
        resultat = appeler_outil(nom_outil, message_utilisateur)
        contexte += f"\n--- {nom_outil} ---\n{resultat}\n"

    # Étape 4 — Assembler le message final
    if contexte:
        message_final = f"Ressources pertinentes :\n{contexte}\n\nQuestion : {message_utilisateur}"
    else:
        message_final = message_utilisateur

    # Étape 5 — Historique + grand LLM
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