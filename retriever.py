import os
from supabase import create_client
import openai

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

def vectoriser(texte):
    response = client.embeddings.create(model="text-embedding-ada-002", input=texte)
    return response.data[0].embedding

def chercher_candidats(question):
    vecteur = vectoriser(question)

    prompts = supabase.rpc("recherche_prompts", {
        "query_embedding": vecteur,
        "match_count": 3
    }).execute().data

    documents = supabase.rpc("recherche_documents", {
        "query_embedding": vecteur,
        "match_count": 3
    }).execute().data

    outils = supabase.rpc("recherche_outils", {
        "query_embedding": vecteur,
        "match_count": 2
    }).execute().data

    return {
        "prompts": prompts,
        "documents": documents,
        "outils": outils
    }