import os
from dotenv import load_dotenv
import requests
from configuration import get_system_prompt
from rag import rechercher_documents
from web import recherche_web
import time

cache_system_prompt = None
cache_timestamp = 0
CACHE_DUREE = 3600  # 1 heure en secondes

def get_system_prompt_cached():
    global cache_system_prompt, cache_timestamp
    maintenant = time.time()
    if cache_system_prompt is None or (maintenant - cache_timestamp) > CACHE_DUREE:
        cache_system_prompt = get_system_prompt()
        cache_timestamp = maintenant
    return cache_system_prompt

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def chat(message_utilisateur):
    system_prompt = get_system_prompt_cached()
    
    mots_cles_academiques = ["exercice", "cours", "théorème", "définition", "démonstration", 
                              "preuve", "calcul", "équation", "fonction", "intégrale", 
                              "dérivée", "matrice", "vecteur", "physique", "chimie", "info",
                              "algorithme", "programme", "code", "expliquer", "comprendre"]
    
    mots_cles_web = ["actualité", "news", "aujourd'hui", "date", "dernier", "récent",
                     "2024", "2025", "2026", "prix", "résultat", "bac", "concours"]
    
    message_lower = message_utilisateur.lower()
    est_academique = any(mot in message_lower for mot in mots_cles_academiques)
    est_web = any(mot in message_lower for mot in mots_cles_web)
    
    if est_web:
        resultats_web = recherche_web(message_utilisateur)
        message_final = f"""Voici des informations trouvées sur internet :

{resultats_web}

Question de l'étudiant : {message_utilisateur}"""

    elif est_academique:
        documents_pertinents = rechercher_documents(message_utilisateur)
        contexte = "\n\n".join(documents_pertinents)
        message_final = f"""Voici des extraits de cours pertinents :

{contexte}

Question de l'étudiant : {message_utilisateur}"""
    else:
        message_final = message_utilisateur
    
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