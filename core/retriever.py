"""
Recherche vectorielle parallèle dans les tables Supabase :
prompts_chunks (via recherche_prompts), documents (via recherche_documents),
outils_chunks (via recherche_outils).
"""

import os
import logging
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client
from embeddings import vectoriser

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : la recherche RAG sera toujours vide.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def chercher_candidats(question):
    try:
        vecteur = vectoriser(question)
    except Exception as e:
        logging.error(f"ERREUR VECTORISATION (OpenRouter) : {e}")
        return {"prompts": [], "documents": [], "outils": []}

    def get_prompts():
        try:
            return supabase.rpc("recherche_prompts", {"query_embedding": vecteur, "match_count": 3}).execute().data
        except Exception as e:
            logging.error(f"ERREUR SUPABASE RPC recherche_prompts : {e}")
            return []

    def get_documents():
        try:
            return supabase.rpc("recherche_documents", {"query_embedding": vecteur, "match_count": 3}).execute().data
        except Exception as e:
            logging.error(f"ERREUR SUPABASE RPC recherche_documents : {e}")
            return []

    def get_outils():
        try:
            return supabase.rpc("recherche_outils", {"query_embedding": vecteur, "match_count": 2}).execute().data
        except Exception as e:
            logging.error(f"ERREUR SUPABASE RPC recherche_outils : {e}")
            return []

    with ThreadPoolExecutor() as executor:
        f_prompts = executor.submit(get_prompts)
        f_documents = executor.submit(get_documents)
        f_outils = executor.submit(get_outils)

    resultat = {
        "prompts": f_prompts.result() or [],
        "documents": f_documents.result() or [],
        "outils": f_outils.result() or []
    }
    logging.info(
        f"RAG -> prompts:{len(resultat['prompts'])} "
        f"documents:{len(resultat['documents'])} "
        f"outils:{len(resultat['outils'])}"
    )
    return resultat
