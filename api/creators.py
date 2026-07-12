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

from typing import Optional

from pydantic import BaseModel

from api.auth import utilisateur_courant, utilisateur_optionnel, supabase

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/creators", tags=["creators"])


class EtatFollow(BaseModel):
    total: int
    suivi_par_moi: bool = False


@router.get("/{creator_id}/follow", response_model=EtatFollow)
def obtenir_etat_follow(creator_id: str, utilisateur=Depends(utilisateur_optionnel)):
    """
    Ajouté pour l'Étape D.4 du pivot social (bouton Follow du portfolio
    créateur) : le POST/DELETE existants ne donnaient aucun moyen de
    savoir si l'utilisateur courant suit déjà ce créateur, ni combien de
    followers il a. Public (compteur visible sans connexion), mais
    `suivi_par_moi` n'est vrai que si un token valide est fourni
    (utilisateur_optionnel, jamais de 401 ici).
    """
    try:
        total_res = (
            supabase.table("follows")
            .select("follower_id", count="exact")
            .eq("creator_id", creator_id)
            .execute()
        )
        total = total_res.count or 0
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage follows creator={creator_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les abonnés pour le moment.")

    suivi_par_moi = False
    if utilisateur is not None:
        try:
            res = (
                supabase.table("follows")
                .select("follower_id")
                .eq("follower_id", utilisateur.id)
                .eq("creator_id", creator_id)
                .maybe_single()
                .execute()
            )
            suivi_par_moi = bool(res and res.data)
        except Exception as e:
            logging.error(
                f"ERREUR SUPABASE (lecture follow follower={utilisateur.id}, creator={creator_id}) : {e}"
            )
            # Best-effort : une erreur ici ne doit pas empêcher d'afficher le total.

    return EtatFollow(total=total, suivi_par_moi=suivi_par_moi)


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
