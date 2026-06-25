import os
import json
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def construire_liste_ressources(prompts_disponibles, pdfs_disponibles, outils_disponibles):
    return f"""
Prompts disponibles : {', '.join(prompts_disponibles)}
PDFs disponibles : {', '.join(pdfs_disponibles)}
Outils disponibles : {', '.join(outils_disponibles)}
"""

def router(question, prompts_disponibles, pdfs_disponibles, outils_disponibles):
    liste = construire_liste_ressources(prompts_disponibles, pdfs_disponibles, outils_disponibles)
    
    system_prompt = """Tu reçois deux choses :
1. Une liste de ressources disponibles (prompts, PDFs, outils)
2. Une question d'un étudiant

Tu dois :
- Lire la question
- Analyser de quoi elle parle (maths ? physique ? actualité ? exo ? cours ?)
- Regarder la liste des ressources disponibles
- Choisir uniquement ce qui est utile pour répondre à cette question
- Si aucune ressource n'est utile, mettre les listes vides et outil aucun

Retourne UNIQUEMENT ce JSON, rien d'autre, aucune explication :
{
  "prompts": ["nom_prompt_choisi"],
  "pdfs": ["nom_pdf_choisi"],
  "outil": "tavily ou aucun",
  "cas": ["cas_précis_dans_le_prompt"]
}"""

    message = f"""Ressources disponibles :
{liste}

Question de l'étudiant : {question}"""

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "meta-llama/llama-3.1-8b-instruct",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ]
        }
    )

    contenu = response.json()["choices"][0]["message"]["content"]
    
    try:
        return json.loads(contenu)
    except:
        return {"prompts": [], "pdfs": [], "outil": "aucun", "cas": []}