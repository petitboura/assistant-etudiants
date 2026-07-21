"""
Synthèse vocale (TTS) via Groq / Orpheus (Canopy Labs).

Particularité par rapport à generation_images.py et
generation_signature.py : la clé nécessaire (GROQ_API_KEY) existe DÉJÀ
dans ce projet, utilisée pour le chat lui-même. Gater cette
fonctionnalité par "la clé existe" ne marcherait donc pas -- elle
existerait toujours. Le gate est un interrupteur dédié,
AUDIO_TTS_ACTIF, qui doit être mis explicitement à "true" par Bourama :
la présence de GROQ_API_KEY pour le chat n'implique PAS un budget ou un
accord pour générer de l'audio en plus.

Modèle utilisé : canopylabs/orpheus-v1-english (statut "Preview" chez
Groq en date du 20/07/2026 -- à surveiller si Groq le fait évoluer).
~22$/million de caractères, à comparer aux ~0,003$/image pour donner un
ordre de grandeur du budget avant d'activer.
"""

import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE = "canopylabs/orpheus-v1-english"
VOIX_PAR_DEFAUT = "austin"


def _get_secret(cle):
    try:
        import streamlit as st
        return st.secrets[cle]
    except Exception:
        return os.environ.get(cle)


def audio_disponible() -> bool:
    """
    Contrairement aux autres modules generation_*.py : vérifie un
    interrupteur dédié (AUDIO_TTS_ACTIF="true"), PAS seulement la
    présence de GROQ_API_KEY (qui existe déjà pour le chat, donc ne
    peut pas servir de gate ici).
    """
    interrupteur = (_get_secret("AUDIO_TTS_ACTIF") or "").strip().lower() == "true"
    return interrupteur and bool(_get_secret("GROQ_API_KEY"))


def generer_audio(texte: str, voix: str = VOIX_PAR_DEFAUT) -> str:
    """
    Convertit du texte en audio (.wav) via Groq/Orpheus, uploade dans
    Supabase Storage, renvoie l'URL publique.

    `texte` peut inclure des indications vocales entre crochets (ex:
    "[cheerful] Bienvenue !") supportées nativement par Orpheus.
    """
    cle = _get_secret("GROQ_API_KEY")
    if not audio_disponible():
        raise RuntimeError(
            "Génération audio indisponible : AUDIO_TTS_ACTIF n'est pas activé, "
            "ou GROQ_API_KEY absente."
        )

    reponse = requests.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        json={"model": MODELE, "input": texte, "voice": voix, "response_format": "wav"},
        timeout=60,
    )
    reponse.raise_for_status()

    chemin = f"audio/{uuid.uuid4()}.wav"
    try:
        supabase.storage.from_(BUCKET).upload(chemin, reponse.content, {"content-type": "audio/wav"})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload audio {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
