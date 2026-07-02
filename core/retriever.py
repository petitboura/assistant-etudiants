"""
Recherche vectorielle parallèle dans les tables Supabase :
prompts_chunks (via recherche_prompts), documents (via recherche_documents),
outils_chunks (via recherche_outils).
"""

import os
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client
from embeddings import vectoriser


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def chercher_candidats(question):
    try:
        vecteur = vectoriser(question)
    except Exception:
        return {"prompts": [], "documents": [], "outils": []}

    def get_prompts():
        try:
            return supabase.rpc("recherche_prompts", {"query_embedding": vecteur, "match_count": 3}).execute().data
        except Exception:
            return []

    def get_documents():
        try:
            return supabase.rpc("recherche_documents", {"query_embedding": vecteur, "match_count": 3}).execute().data
        except Exception:
            return []

    def get_outils():
        try:
            return supabase.rpc("recherche_outils", {"query_embedding": vecteur, "match_count": 2}).execute().data
        except Exception:
            return []

    with ThreadPoolExecutor() as executor:
        f_prompts = executor.submit(get_prompts)
        f_documents = executor.submit(get_documents)
        f_outils = executor.submit(get_outils)

    return {
        "prompts": f_prompts.result() or [],
        "documents": f_documents.result() or [],
        "outils": f_outils.result() or []
    }
