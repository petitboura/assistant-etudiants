"""
Section "Mes comportements" -- lecture/écriture du texte libre écrit par
l'étudiant (2026-08-06, demande Bourama). Isolé de
core/contenu_dynamique_matiere.py : ce texte s'applique EN PLUS du
system_prompt déjà résolu (généraliste, matière d'un enseignant, ou
"sans enseignant"), quel que soit l'agent -- pas seulement les agents à
contenu dynamique par matière. Voir l'injection dans
core/main.py::_construire_system_prompt et les endpoints dans
api/comportements_etudiants.py.
"""

import logging
import os

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SECRET = os.environ["SUPABASE_SECRET"]
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

logging.basicConfig(level=logging.INFO)


def lire_comportement(agent_id: str, etudiant_id: str) -> str | None:
    """None si rien n'est enregistré (pas de bruit inutile dans le prompt
    ni de ligne vide affichée côté frontend)."""
    try:
        res = (
            supabase.table("comportements_etudiants")
            .select("texte")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", etudiant_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture comportement {agent_id}/{etudiant_id}) : {e}")
        return None
    texte = (res.data or {}).get("texte") if res else None
    return texte.strip() if texte and texte.strip() else None


def enregistrer_comportement(agent_id: str, etudiant_id: str, texte: str) -> str:
    texte = texte.strip()
    supabase.table("comportements_etudiants").upsert(
        {"agent_id": agent_id, "etudiant_id": etudiant_id, "texte": texte},
        on_conflict="agent_id,etudiant_id",
    ).execute()
    return texte
