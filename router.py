import os
import json
import requests

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def router(question, candidats):
    prompts = candidats["prompts"]
    documents = candidats["documents"]
    outils = candidats["outils"]

    system_prompt = """Tu reçois une question d'un étudiant et des candidats pré-filtrés.
Tu dois choisir uniquement ce qui est vraiment utile pour répondre.

Retourne UNIQUEMENT ce JSON, rien d'autre :
{
  "prompts": ["contenu_prompt_choisi"],
  "documents": ["contenu_document_choisi"],
  "outils": ["nom_outil_choisi"]
}"""

    message = f"""Question : {question}

Prompts candidats :
{json.dumps([{"contenu": p["contenu"]} for p in prompts], ensure_ascii=False)}

Documents candidats :
{json.dumps([{"nom": d["nom"], "contenu": d["contenu"]} for d in documents], ensure_ascii=False)}

Outils disponibles :
{json.dumps([{"nom": o["nom"], "description": o["description"]} for o in outils], ensure_ascii=False)}"""

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
        return {"prompts": [], "documents": [], "outils": []}