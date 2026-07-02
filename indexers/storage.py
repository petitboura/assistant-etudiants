"""
Utilitaires de gestion des documents dans le stockage Supabase
(bucket "IA pour etudiants").
"""

import os
from supabase import create_client


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

BUCKET = "IA pour etudiants"


def upload_document(file_path, file_name):
    with open(file_path, "rb") as f:
        supabase.storage.from_(BUCKET).upload(file_name, f)
    print(f"{file_name} uploadé avec succès")


def list_documents():
    files = supabase.storage.from_(BUCKET).list()
    return [f["name"] for f in files]


def delete_document(file_name):
    supabase.storage.from_(BUCKET).remove([file_name])
    print(f"{file_name} supprimé")


def get_document_url(file_name):
    return supabase.storage.from_(BUCKET).get_public_url(file_name)
