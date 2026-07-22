"""
Génération d'images -- DEUX fournisseurs, pas un seul :

1. Pollinations.ai (https://pollinations.ai) : GRATUIT, SANS CLÉ,
   actif par défaut. Génère de vraies images FLUX via une simple URL.
   Aucune garantie de disponibilité/qualité de service contractuelle
   (pas de SLA), mais gratuit et suffisant pour tester ou pour un usage
   léger -- voir échange avec Bourama du 2026-07-21 ("y a il pas une
   alternative gratuite").

2. Together AI (Flux Schnell), ~0,003$/image : utilisé UNIQUEMENT si
   TOGETHER_API_KEY est configurée. Meilleure fiabilité/qualité pour un
   usage à plus grand volume, mais payant.

Contrairement à la version précédente de ce fichier, l'outil n'est
DONC PLUS jamais totalement invisible pour l'agent : il fonctionne dès
maintenant via Pollinations, et bascule automatiquement vers Together
AI si la clé est ajoutée plus tard (meilleure qualité, pas de
changement de code nécessaire).

NON TESTÉ EN CONDITIONS RÉELLES (le domaine image.pollinations.ai
n'était pas accessible depuis l'environnement de développement au
moment de l'écriture, 21/07/2026 -- restriction de bac à sable, pas un
problème connu de Pollinations). À vérifier au premier vrai test,
comme d'habitude.
"""

import logging
import os
import urllib.parse
import uuid

import requests

from api.auth import supabase

BUCKET = "generations"
MODELE_TOGETHER = "black-forest-labs/FLUX.1-schnell"


def _get_secret(cle):
    try:
        import streamlit as st
        return st.secrets[cle]
    except Exception:
        return os.environ.get(cle)


def image_generation_disponible() -> bool:
    """
    Toujours True maintenant (Pollinations ne demande rien). Fonction
    conservée (plutôt que supprimée) pour ne pas casser les appels
    existants dans serveur_mcp_generation.py et api/generation.py --
    et parce que ça reste un point d'extension utile si jamais on
    voulait un jour un mode "désactivé de force".
    """
    return True


def _generer_via_pollinations(prompt: str) -> bytes:
    prompt_encode = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{prompt_encode}?width=1024&height=1024&nologo=true"
    reponse = requests.get(url, timeout=60)
    reponse.raise_for_status()
    return reponse.content


def _generer_via_together(prompt: str, cle: str) -> bytes:
    reponse = requests.post(
        "https://api.together.xyz/v1/images/generations",
        headers={"Authorization": f"Bearer {cle}"},
        json={"model": MODELE_TOGETHER, "prompt": prompt, "n": 1, "width": 1024, "height": 1024},
        timeout=60,
    )
    reponse.raise_for_status()
    url_image_temporaire = reponse.json()["data"][0]["url"]
    return requests.get(url_image_temporaire, timeout=30).content


def generer_image(prompt: str) -> str:
    """
    Utilise Together AI si TOGETHER_API_KEY est configurée (meilleure
    qualité, payant), sinon retombe automatiquement sur Pollinations
    (gratuit, sans clé). Uploade le résultat dans Supabase Storage,
    renvoie l'URL publique.
    """
    cle_together = _get_secret("TOGETHER_API_KEY")

    if cle_together:
        image_bytes = _generer_via_together(prompt, cle_together)
    else:
        image_bytes = _generer_via_pollinations(prompt)

    chemin = f"images/{uuid.uuid4()}.png"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin, image_bytes, {"content-type": "image/png"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload image {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)

