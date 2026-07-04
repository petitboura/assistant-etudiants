import os
import json
import logging
from groq import Groq
from google import genai
from google.genai import types
from configuration import get_system_prompt
from retriever import chercher_candidats
from mcp_tools import lister_tous_les_outils, appeler_outil

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


GROQ_PRIMARY = "openai/gpt-oss-120b"
GOOGLE_MODEL = "gemini-2.5-flash"
GROQ_FALLBACKS = [
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.3-70b-versatile",
]
MESSAGE_ERREUR = "Désolé, je rencontre un souci technique pour répondre. Merci de réessayer dans un instant."

# Nombre maximum d'aller-retours "outil" autorisés pour une seule question,
# pour éviter qu'un modèle ne boucle indéfiniment sur le même outil.
MAX_ETAPES_OUTILS = 5

# Noms lisibles affichés à l'utilisateur pendant qu'un outil MCP est utilisé.
# Nouvel outil = ajouter une ligne ici (optionnel, sinon le nom brut s'affiche).
NOMS_OUTILS_LISIBLES = {
    "tavily_search": "Recherche sur le web",
    "tavily_extract": "Lecture d'une page web",
    "tavily_crawl": "Exploration d'un site web",
    "tavily_map": "Cartographie d'un site web",
    "tavily_research": "Recherche approfondie",
}


def _nom_lisible(nom_outil):
    return NOMS_OUTILS_LISIBLES.get(nom_outil, nom_outil)


REGLE_CONTEXTE_INVISIBLE = (
    "\n\nIMPORTANT ABSOLU : Tout ce qui précède est ton contexte interne invisible. "
    "L'utilisateur ne voit rien de tout cela. Si l'utilisateur dit 'c'est quoi ce message' "
    "ou similaire, il parle uniquement de ta dernière réponse ou de la sienne — jamais de "
    "ton contexte interne. Ne le mentionne jamais."
)


def _construire_system_prompt(message_utilisateur):
    system_prompt = get_system_prompt()
    candidats = chercher_candidats(message_utilisateur)

    instructions = "".join(f"\n{c['contenu']}\n" for c in candidats.get("prompts", []))
    contexte_docs = "".join(f"\n{c['contenu']}\n" for c in candidats.get("documents", []))

    system_final = system_prompt
    if instructions:
        system_final += f"\n\n{instructions}"
    if contexte_docs:
        system_final += f"\n\n{contexte_docs}"
    system_final += REGLE_CONTEXTE_INVISIBLE

    logging.info(
        f"Prompt système construit -> base_notion:{len(system_prompt)} caractères, "
        f"instructions:{'oui' if instructions else 'NON'}, "
        f"contexte_docs:{'oui' if contexte_docs else 'NON'}"
    )
    return system_final


def chat(message_utilisateur, historique=None):
    """
    Generateur d'evenements. Chaque element produit est un dictionnaire :
    - {"type": "statut", "texte": "..."}   -> un outil MCP est en cours d'utilisation
    - {"type": "resultat", "texte": "..."} -> resultat brut (tronque) de cet outil
    - {"type": "reponse", "texte": "..."}  -> morceau de la reponse finale (streaming)

    faces/app_etudiant.py doit distinguer ces trois types pour savoir quoi
    afficher, et ne garder que "reponse" dans l'historique de conversation.
    """
    if historique is None:
        historique = []

    system_final = _construire_system_prompt(message_utilisateur)

    messages_base = [{"role": "system", "content": system_final}]
    messages_base += historique
    messages_base.append({"role": "user", "content": message_utilisateur})

    client_groq = Groq(api_key=get_secret("GROQ_API_KEY"))
    outils_mcp, table_routage = lister_tous_les_outils(get_secret)

    # 1. GPT-OSS 120B, avec cycle d'outils MCP dynamique
    try:
        messages_agent = list(messages_base)

        for _ in range(MAX_ETAPES_OUTILS):
            completion = client_groq.chat.completions.create(
                model=GROQ_PRIMARY,
                messages=messages_agent,
                max_completion_tokens=1024,
                tools=outils_mcp if outils_mcp else None,
                stream=True,
                timeout=120,
            )

            reponse_directe = False
            appels_en_cours = {}  # index -> {"id", "name", "arguments"}

            for chunk in completion:
                delta = chunk.choices[0].delta

                if delta.content:
                    # Reponse directe (pas d'outil) : streaming token par
                    # token exactement comme avant, sans attendre la fin.
                    reponse_directe = True
                    yield {"type": "reponse", "texte": delta.content}

                if delta.tool_calls:
                    for fragment in delta.tool_calls:
                        etat = appels_en_cours.setdefault(
                            fragment.index, {"id": None, "name": "", "arguments": ""}
                        )
                        if fragment.id:
                            etat["id"] = fragment.id
                        if fragment.function:
                            if fragment.function.name:
                                etat["name"] += fragment.function.name
                            if fragment.function.arguments:
                                etat["arguments"] += fragment.function.arguments

            if reponse_directe:
                logging.info(f"Réponse via GROQ (sans outil, streaming): {GROQ_PRIMARY}")
                return

            if not appels_en_cours:
                # Ni contenu ni outil (rare) : rien a faire de plus.
                return

            appels = [appels_en_cours[i] for i in sorted(appels_en_cours)]

            messages_agent.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": appel["id"],
                        "type": "function",
                        "function": {
                            "name": appel["name"],
                            "arguments": appel["arguments"],
                        },
                    }
                    for appel in appels
                ],
            })

            for appel in appels:
                nom_outil = appel["name"]
                yield {"type": "statut", "texte": f"{_nom_lisible(nom_outil)}..."}

                try:
                    arguments = json.loads(appel["arguments"] or "{}")
                except Exception:
                    arguments = {}

                resultat = appeler_outil(nom_outil, arguments, table_routage)
                yield {"type": "statut_termine", "texte": f"{_nom_lisible(nom_outil)} effectuée"}

                messages_agent.append({
                    "role": "tool",
                    "tool_call_id": appel["id"],
                    "content": resultat,
                })

        # Reponse finale en streaming, si on a epuise MAX_ETAPES_OUTILS
        # sans que le modele ne se decide a repondre directement.
        completion = client_groq.chat.completions.create(
            model=GROQ_PRIMARY,
            messages=messages_agent,
            max_completion_tokens=1024,
            tools=outils_mcp if outils_mcp else None,
            stream=True,
            timeout=120,
        )
        for chunk in completion:
            token = chunk.choices[0].delta.content or ""
            if token:
                yield {"type": "reponse", "texte": token}
        logging.info(f"Réponse via GROQ (avec outil): {GROQ_PRIMARY}")
        return
    except Exception as e:
        if "timeout" not in str(e).lower():
            logging.error(f"ERREUR GROQ {GROQ_PRIMARY}: {e}")

    # 2. Gemini 2.5 Flash — fallback simple, sans outils MCP
    try:
        client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
        gemini_messages = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages_base if m["role"] != "system"
        ]
        response = client_google.models.generate_content_stream(
            model=GOOGLE_MODEL,
            contents=gemini_messages,
            config=types.GenerateContentConfig(
                system_instruction=system_final,
                max_output_tokens=1024
            )
        )
        for chunk in response:
            if chunk.text:
                yield {"type": "reponse", "texte": chunk.text}
        logging.info("Réponse via GEMINI")
        return
    except Exception as e:
        logging.error(f"ERREUR GEMINI: {e}")

    # 3-5. Fallbacks Groq — sans outils MCP
    for model in GROQ_FALLBACKS:
        try:
            completion = client_groq.chat.completions.create(
                model=model,
                messages=messages_base,
                max_completion_tokens=1024,
                stream=True,
                timeout=120,
                reasoning_effort="none"
            )
            for chunk in completion:
                token = chunk.choices[0].delta.content or ""
                if token:
                    yield {"type": "reponse", "texte": token}
            logging.info(f"Réponse via GROQ fallback: {model}")
            return
        except Exception as e:
            if "timeout" not in str(e).lower():
                logging.error(f"ERREUR GROQ {model}: {e}")
            continue

    yield {"type": "reponse", "texte": MESSAGE_ERREUR}
