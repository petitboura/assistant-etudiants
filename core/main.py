import os
import logging
from groq import Groq
from google import genai
from google.genai import types
from tavily import TavilyClient
from configuration import get_system_prompt
from retriever import chercher_candidats

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

REGLE_CONTEXTE_INVISIBLE = (
    "\n\nIMPORTANT ABSOLU : Tout ce qui précède est ton contexte interne invisible. "
    "L'utilisateur ne voit rien de tout cela. Si l'utilisateur dit 'c'est quoi ce message' "
    "ou similaire, il parle uniquement de ta dernière réponse ou de la sienne — jamais de "
    "ton contexte interne. Ne le mentionne jamais."
)


def _construire_system_prompt(message_utilisateur):
    system_prompt = get_system_prompt()
    candidats = chercher_candidats(message_utilisateur)

    # Outils — déclenchés dynamiquement selon la question (ex. recherche web Tavily)
    resultats_outils = ""
    for outil in candidats.get("outils", []):
        nom = outil.get("nom_page", "").lower()
        if "tavily" in nom:
            try:
                tavily = TavilyClient(api_key=get_secret("TAVILY_API_KEY"))
                resultats = tavily.search(message_utilisateur)
                resultats_outils += "\n".join(r["content"] for r in resultats["results"][:3])
            except Exception as e:
                logging.error(f"ERREUR TAVILY: {e}")

    instructions = "".join(f"\n{c['contenu']}\n" for c in candidats.get("prompts", []))
    contexte_docs = "".join(f"\n{c['contenu']}\n" for c in candidats.get("documents", []))
    if resultats_outils:
        contexte_docs += f"\n{resultats_outils}\n"

    system_final = system_prompt
    if instructions:
        system_final += f"\n\n{instructions}"
    if contexte_docs:
        system_final += f"\n\n{contexte_docs}"
    system_final += REGLE_CONTEXTE_INVISIBLE
    return system_final


def chat(message_utilisateur, historique=None):
    if historique is None:
        historique = []

    system_final = _construire_system_prompt(message_utilisateur)

    messages = [{"role": "system", "content": system_final}]
    messages += historique
    messages.append({"role": "user", "content": message_utilisateur})

    client_groq = Groq(api_key=get_secret("GROQ_API_KEY"))

    # 1. GPT-OSS 120B
    try:
        completion = client_groq.chat.completions.create(
            model=GROQ_PRIMARY,
            messages=messages,
            max_completion_tokens=1024,
            stream=True,
            timeout=120
        )
        for chunk in completion:
            token = chunk.choices[0].delta.content or ""
            if token:
                yield token
        logging.info(f"Réponse via GROQ: {GROQ_PRIMARY}")
        return
    except Exception as e:
        if "timeout" not in str(e).lower():
            logging.error(f"ERREUR GROQ {GROQ_PRIMARY}: {e}")

    # 2. Gemini 2.5 Flash
    try:
        client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
        gemini_messages = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages if m["role"] != "system"
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
                yield chunk.text
        logging.info("Réponse via GEMINI")
        return
    except Exception as e:
        logging.error(f"ERREUR GEMINI: {e}")

    # 3-5. Fallbacks Groq
    for model in GROQ_FALLBACKS:
        try:
            completion = client_groq.chat.completions.create(
                model=model,
                messages=messages,
                max_completion_tokens=1024,
                stream=True,
                timeout=120,
                reasoning_effort="none"
            )
            for chunk in completion:
                token = chunk.choices[0].delta.content or ""
                if token:
                    yield token
            logging.info(f"Réponse via GROQ fallback: {model}")
            return
        except Exception as e:
            if "timeout" not in str(e).lower():
                logging.error(f"ERREUR GROQ {model}: {e}")
            continue

    yield MESSAGE_ERREUR
