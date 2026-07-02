"""
Indexation de documents PDF (stockés dans Supabase Storage) vers la table
`documents`, utilisée pour la recherche RAG côté core/retriever.py.

Lancé manuellement quand un nouveau document doit être ajouté à la base
de connaissance :
    python index_documents.py livre-algebre-1.pdf
"""

import os
import sys
import PyPDF2
from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
from embeddings import vectoriser, decouper_texte  # noqa: E402
from storage import BUCKET, supabase as storage_client  # noqa: E402


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def extraire_texte_pdf(chemin_pdf):
    texte = ""
    with open(chemin_pdf, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            texte += page.extract_text() + "\n"
    return texte.replace("\x00", "")


def indexer_document(chemin_pdf, nom_fichier):
    print(f"Lecture de {nom_fichier}...")
    texte = extraire_texte_pdf(chemin_pdf)
    morceaux = decouper_texte(texte)

    print(f"Indexation de {len(morceaux)} morceaux...")
    for morceau in morceaux:
        embedding = vectoriser(morceau)
        supabase.table("documents").insert({
            "nom": nom_fichier,
            "contenu": morceau,
            "embedding": embedding
        }).execute()

    print(f"{nom_fichier} indexé avec succès !")


def indexer_depuis_supabase(nom_fichier):
    print(f"Téléchargement de {nom_fichier} depuis Supabase...")
    response = storage_client.storage.from_(BUCKET).download(nom_fichier)

    chemin_temp = f"temp_{nom_fichier}"
    with open(chemin_temp, "wb") as f:
        f.write(response)

    indexer_document(chemin_temp, nom_fichier)

    os.remove(chemin_temp)
    print("Fichier temporaire supprimé.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python index_documents.py <nom_fichier.pdf>")
    else:
        indexer_depuis_supabase(sys.argv[1])
