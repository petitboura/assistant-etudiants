"""
Génération d'images via Flux Schnell, hébergé par Together AI
(~0,003$/image, le moins cher du marché en juillet 2026 -- voir échange
avec Bourama du 2026-07-20).

CONTRAIREMENT à generation_documents.py et generation_code.py, cette
fonctionnalité N'EST PAS gratuite : chaque appel coûte de l'argent à
Together AI. Elle est donc gatée par la présence de TOGETHER_API_KEY dans
les secrets/variables d'environnement.

Tant que Bourama n'a pas ajouté cette clé (Railway -> variables
d'environnement -> TOGETHER_API_KEY), `image_generation_disponible()`
renvoie False et le serveur MCP (voir serveur_mcp_generation.py) ne
propose même pas l'outil à l'agent -- le bouton frontend, lui, doit
gérer ce cas et afficher "bientôt disponible" plutôt que d'appeler cette
fonction à l'aveugle (voir api/generation.py).

Le jour où la clé est ajoutée : rien d'autre à faire, tout s'active tout
seul, aucune ligne de code à retoucher ici.
"""

import logging
import os
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE = "black-forest-labs/FLUX.1-schnell"


def _get_secret(cle):
    try:
        import streamlit as st
        return st.secrets[cle]
    except Exception:
        return os.environ.get(cle)


def image_generation_disponible() -> bool:
    """
    Utilisé par le serveur MCP (pour décider si l'outil doit être
    proposé à l'agent) et par api/generation.py (pour répondre "pas
    encore disponible" plutôt que planter en pleine requête utilisateur).
    """
    return bool(_get_secret("TOGETHER_API_KEY"))


def generer_image(prompt: str) -> str:
    """
    Appelle Together AI (Flux Schnell), uploade le résultat dans Supabase
    Storage, renvoie l'URL publique.

    Lève une exception si la clé est absente (l'appelant doit avoir déjà
    vérifié image_generation_disponible() avant d'appeler cette fonction
    -- ce garde-fou est volontairement redondant, pas une excuse pour
    sauter la vérification en amont) ou si l'appel/l'upload échoue.
    """
    cle = _get_secret("TOGETHER_API_KEY")
    if not cle:
        raise RuntimeError(
            "Génération d'image indisponible : TOGETHER_API_KEY n'est pas configurée."
        )

    reponse = requests.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {cle}"},
        json={"model": MODELE, "prompt": prompt, "n": 1, "width": 1024, "height": 1024},
        timeout=60,
    )
    reponse.raise_for_status()
    url_image_temporaire = reponse.json()["data"][0]["url"]

    image_bytes = requests.get(url_image_temporaire, timeout=30).content

    chemin = f"images/{uuid.uuid4()}.png"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin, image_bytes, {"content-type": "image/png"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload image {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
