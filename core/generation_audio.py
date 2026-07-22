"""
Synthèse vocale (TTS) -- DEUX fournisseurs, même logique que
generation_images.py (Pollinations/Together) :

1. microsoft/speecht5_tts via Hugging Face (router.huggingface.co),
   GRATUIT : nécessite un compte Hugging Face (gratuit, sans carte
   bancaire) et un token (HF_API_TOKEN). Utilisé PAR DÉFAUT si
   HF_API_TOKEN est configurée.

   CORRECTIF du 22/07/2026 : le plan initial utilisait Kokoro-82M, qui
   a échoué en test réel avec une erreur 403 -- Kokoro n'est en fait
   PAS déployé sur l'infrastructure d'inférence de Hugging Face (ni
   gratuite ni payante), voir la doc HF/discussions communautaires.
   speecht5_tts est le modèle que HF documente eux-mêmes comme exemple
   officiel pour hf-inference (donc confirmé "warm"/servi), qualité
   moindre que Kokoro mais réellement fonctionnel.

2. Groq / Orpheus, payant (~22$/million de caractères) : utilisé
   UNIQUEMENT si AUDIO_TTS_ACTIF="true" ET GROQ_API_KEY présente
   (déjà là pour le chat, mais gatée par un interrupteur dédié -- voir
   ancienne version de ce fichier). Meilleure latence/fiabilité pour
   un usage à volume.

Si aucune des deux clés n'est configurée : indisponible, comme avant.
"""

import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE_GROQ = "canopylabs/orpheus-v1-english"
MODELE_HF = "microsoft/speecht5_tts"
VOIX_PAR_DEFAUT = "austin"


def _get_secret(cle):
    try:
        import streamlit as st
        return st.secrets[cle]
    except Exception:
        return os.environ.get(cle)


def _groq_actif() -> bool:
    interrupteur = (_get_secret("AUDIO_TTS_ACTIF") or "").strip().lower() == "true"
    return interrupteur and bool(_get_secret("GROQ_API_KEY"))


def audio_disponible() -> bool:
    return bool(_get_secret("HF_API_TOKEN")) or _groq_actif()


def _generer_via_huggingface(texte: str) -> bytes:
    token = _get_secret("HF_API_TOKEN")
    reponse = requests.post(
        f"https://router.huggingface.co/hf-inference/models/{MODELE_HF}",
        headers={"Authorization": f"Bearer {token}"},
        json={"text_inputs": texte},
        timeout=60,
    )
    reponse.raise_for_status()
    return reponse.content


def _generer_via_groq(texte: str, voix: str) -> bytes:
    cle = _get_secret("GROQ_API_KEY")
    reponse = requests.post(
        "https://api.groq.com/openai/v1/audio/speech",
        headers={"Authorization": f"Bearer {cle}", "Content-Type": "application/json"},
        json={"model": MODELE_GROQ, "input": texte, "voice": voix, "response_format": "wav"},
        timeout=60,
    )
    reponse.raise_for_status()
    return reponse.content


def generer_audio(texte: str, voix: str = VOIX_PAR_DEFAUT) -> str:
    """
    Utilise Groq/Orpheus si explicitement activé (AUDIO_TTS_ACTIF=true,
    payant, meilleure latence), sinon Hugging Face/speecht5_tts
    (gratuit, nécessite juste HF_API_TOKEN). Uploade dans Supabase
    Storage, renvoie l'URL publique.

    `voix` n'est utilisé que par le chemin Groq -- speecht5_tts utilise
    sa propre voix par défaut côté Hugging Face.
    """
    if _groq_actif():
        audio_bytes = _generer_via_groq(texte, voix)
    elif _get_secret("HF_API_TOKEN"):
        audio_bytes = _generer_via_huggingface(texte)
    else:
        raise RuntimeError(
            "Génération audio indisponible : ni HF_API_TOKEN (gratuit) ni "
            "AUDIO_TTS_ACTIF+GROQ_API_KEY (payant) ne sont configurés."
        )

    chemin = f"audio/{uuid.uuid4()}.wav"
    try:
        supabase.storage.from_(BUCKET).upload(chemin, audio_bytes, {"content-type": "audio/wav"})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload audio {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
