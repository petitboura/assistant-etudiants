"""
Étape C du plan (voir PIVOT_SOCIAL.md) : suivre/ne plus suivre un
créateur (table `follows`), pour le bouton Follow du portfolio
créateur (`/u/[slug]`, Étape E).

Volontairement dans un fichier séparé de `api/agents.py` : ces routes
portent sur un créateur (`user_id`), pas sur un agent, même si les deux
router sur `/api/...` — les regrouper dans `agents.py` aurait mélangé
deux ressources différentes dans un seul fichier déjà long.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.auth import utilisateur_courant, supabase

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/creators", tags=["creators"])


@router.post("/{creator_id}/follow", status_code=204)
def suivre_createur(creator_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Suit un créateur (table `follows`, contrainte unique
    `(follower_id, creator_id)` — voir PIVOT_SOCIAL.md, section "Modèle
    de données"). Upsert (idempotent) plutôt qu'insert : suivre deux fois
    de suite ne doit pas renvoyer une erreur 409 côté frontend, juste ne
    rien changer de plus.
    """
    if creator_id == utilisateur.id:
        raise HTTPException(status_code=422, detail="Impossible de se suivre soi-même.")

    try:
        supabase.table("follows").upsert(
            {"follower_id": utilisateur.id, "creator_id": creator_id},
            on_conflict="follower_id,creator_id",
        ).execute()
    except Exception as e:
        logging.error(
            f"ERREUR SUPABASE (upsert follow follower={utilisateur.id}, creator={creator_id}) : {e}"
        )
        raise HTTPException(status_code=500, detail="Impossible de suivre ce créateur pour le moment.")


@router.delete("/{creator_id}/follow", status_code=204)
def ne_plus_suivre_createur(creator_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Retire un follow existant. Idempotent aussi : ne pas suivre quelqu'un
    puis appeler ce endpoint ne renvoie pas d'erreur, `delete()` sur une
    ligne absente ne fait simplement rien côté Supabase.
    """
    try:
        (
            supabase.table("follows")
            .delete()
            .eq("follower_id", utilisateur.id)
            .eq("creator_id", creator_id)
            .execute()
        )
    except Exception as e:
        logging.error(
            f"ERREUR SUPABASE (delete follow follower={utilisateur.id}, creator={creator_id}) : {e}"
        )
        raise HTTPException(status_code=500, detail="Impossible de retirer ce follow pour le moment.")
