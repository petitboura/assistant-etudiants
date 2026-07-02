"""
Fonctions de vectorisation partagées par tous les modules du projet
(retriever, indexers).

Utilise le modèle d'embedding OpenAI text-embedding-ada-002 via OpenRouter,
pour rester compatible avec les vecteurs déjà stockés dans Supabase
(changer de modèle changerait la dimension des vecteurs et casserait
la recherche existante).
"""

import os
import openai


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = openai.OpenAI(
            api_key=get_secret("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1"
        )
    return _client


def vectoriser(texte):
    response = _get_client().embeddings.create(model="text-embedding-ada-002", input=texte)
    return response.data[0].embedding


def decouper_texte(texte, taille=500):
    """Découpe un texte en morceaux de `taille` mots."""
    mots = texte.split()
    return [" ".join(mots[i:i + taille]) for i in range(0, len(mots), taille)] or [""]
