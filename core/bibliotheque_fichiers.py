"""
Bibliothèque de fichiers uploadés, persistante, à 3 niveaux d'accès :
- "plateforme"  : uploadé par Bourama, visible par TOUS les agents
- "agent"       : uploadé par le créateur d'un agent, visible par tous
                   les utilisateurs de CET agent précis
- "utilisateur" : uploadé par un utilisateur dans le chat, visible par
                   lui seul

Remplace le comportement précédent où un fichier uploadé (image ou
document) n'était utilisé qu'une seule fois puis jeté (voir
api/uploads.py avant le 2026-07-22) : ici, tout fichier est conservé
dans Supabase Storage (bucket "bibliotheque") ET indexé dans la table
fichiers_uploades, pour qu'un agent puisse le retrouver et le
redonner/afficher plus tard, y compris dans une autre conversation.

Un seul type de fichier n'est pas privilégié : image, PDF, audio,
vidéo... tout passe par le même mécanisme, seule la description texte
(fournie à l'upload) permet à l'IA de savoir ce que contient le fichier
sans avoir besoin de l'ouvrir.
"""

import logging
import os
import uuid

from supabase import create_client

BUCKET = "bibliotheque"


def _get_secret(cle):
    try:
        import streamlit as st
        return st.secrets[cle]
    except Exception:
        return os.environ.get(cle)


supabase = create_client(_get_secret("SUPABASE_URL"), _get_secret("SUPABASE_SECRET"))


def enregistrer_fichier(
    contenu: bytes,
    nom_fichier: str,
    type_mime: str,
    niveau: str,
    uploade_par: str,
    agent_id: str = None,
    user_id: str = None,
    description: str = None,
) -> dict:
    """
    Stocke un fichier dans Supabase Storage et l'indexe dans
    fichiers_uploades. `niveau` doit être "plateforme", "agent" ou
    "utilisateur" (voir docstring du module) ; `agent_id`/`user_id` sont
    requis en cohérence avec le niveau (ex. niveau="agent" -> agent_id
    obligatoire) mais ce n'est pas vérifié ici -- c'est à l'appelant
    (route API) de garantir la cohérence selon qui uploade.
    Renvoie la ligne insérée (avec son id et son url_publique).
    """
    extension = nom_fichier.rsplit(".", 1)[-1] if "." in nom_fichier else "bin"
    chemin_stockage = f"{niveau}/{uuid.uuid4()}.{extension}"

    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, contenu, {"content-type": type_mime}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload bibliothèque {chemin_stockage}) : {e}")
        raise

    url_publique = supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)

    try:
        insertion = supabase.table("fichiers_uploades").insert({
            "niveau": niveau,
            "agent_id": agent_id,
            "user_id": user_id,
            "uploade_par": uploade_par,
            "chemin_stockage": chemin_stockage,
            "url_publique": url_publique,
            "nom_fichier": nom_fichier,
            "type_mime": type_mime,
            "description": description,
            "taille_octets": len(contenu),
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE fichiers_uploades ({chemin_stockage}) : {e}")
        raise

    return insertion.data[0]


def chercher_fichiers(recherche: str, agent_id: str = None, user_id: str = None, limite: int = 10) -> list:
    """
    Cherche des fichiers accessibles dans le contexte courant (agent_id
    + user_id de la conversation en cours), tous niveaux confondus,
    triés du plus spécifique au plus large : utilisateur -> agent ->
    plateforme. `recherche` filtre sur le nom de fichier ou la
    description (recherche texte simple, pas de recherche sémantique
    pour l'instant).

    Un utilisateur non connecté (user_id=None) ne voit que les niveaux
    agent et plateforme -- pas d'erreur, juste moins de résultats.
    """
    niveaux_accessibles = ["plateforme"]
    if agent_id:
        niveaux_accessibles.append("agent")
    if user_id:
        niveaux_accessibles.append("utilisateur")

    requete = (
        supabase.table("fichiers_uploades")
        .select("*")
        .in_("niveau", niveaux_accessibles)
        .or_(f"nom_fichier.ilike.%{recherche}%,description.ilike.%{recherche}%")
        .limit(limite)
    )

    resultat = requete.execute()

    # Filtre applicatif : "agent" ne doit remonter que les fichiers du
    # bon agent, "utilisateur" que ceux du bon user -- .in_("niveau",...)
    # seul ne suffit pas à scoper correctement, ça ne fait que dire
    # quels NIVEAUX sont autorisés, pas QUEL agent/user précisément.
    fichiers = [
        f for f in resultat.data
        if f["niveau"] == "plateforme"
        or (f["niveau"] == "agent" and f["agent_id"] == agent_id)
        or (f["niveau"] == "utilisateur" and f["user_id"] == user_id)
    ]

    ordre_priorite = {"utilisateur": 0, "agent": 1, "plateforme": 2}
    fichiers.sort(key=lambda f: ordre_priorite[f["niveau"]])
    return fichiers
