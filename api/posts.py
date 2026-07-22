"""
Article / Réflexion / Histoire (demande Bourama, 2026-07-15 : "brancher la
fonction article et la définir" + section Histoire/Réflexion). Une seule
table `posts` avec discriminant `type` (voir migration
`pivot_social_posts_article_reflexion_histoire`), une seule famille
d'endpoints pour les 3 -- le feed de l'accueil (3 onglets) et le profil
créateur (3 mêmes sections) font tous la même requête filtrée par type.

Règles par type (définies avec Bourama le 2026-07-15) :
- article   : titre obligatoire, contenu (long texte) obligatoire, image
              de couverture OPTIONNELLE, aucune photo supplémentaire.
- reflexion : pas de titre, contenu (message court) obligatoire, aucune
              image, aucune photo supplémentaire.
- histoire  : titre obligatoire, contenu (légende) obligatoire, image de
              couverture OBLIGATOIRE, jusqu'à 3 photos supplémentaires
              (optionnelles).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/posts", tags=["posts"])

TYPES_VALIDES = {"article", "reflexion", "histoire"}
MAX_PHOTOS_SUPPLEMENTAIRES = 3


class PostCree(BaseModel):
    type: str
    titre: Optional[str] = None
    contenu: str
    image_couverture_url: Optional[str] = None
    photos_supplementaires: List[str] = Field(default_factory=list)


class Post(BaseModel):
    id: int
    user_id: str
    nom_affiche: Optional[str] = None
    avatar_url: Optional[str] = None
    type: str
    titre: Optional[str] = None
    contenu: str
    image_couverture_url: Optional[str] = None
    photos_supplementaires: List[str] = Field(default_factory=list)
    created_at: Optional[str] = None


def _resoudre_profils(user_ids: List[str]) -> dict:
    """Une seule requête groupée, même logique que api/agents.py:lister_commentaires."""
    if not user_ids:
        return {}
    try:
        res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, avatar_url")
            .in_("user_id", list(set(user_ids)))
            .execute()
        )
        return {p["user_id"]: p for p in (res.data or [])}
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (résolution profils posts) : {e}")
        return {}


@router.post("", response_model=Post, status_code=201)
def creer_post(payload: PostCree, request: Request, utilisateur=Depends(utilisateur_courant)):
    if payload.type not in TYPES_VALIDES:
        raise HTTPException(status_code=422, detail="Type de publication invalide.")

    contenu = payload.contenu.strip()
    if not contenu:
        raise HTTPException(status_code=422, detail="Le contenu ne peut pas être vide.")

    titre = (payload.titre or "").strip() or None

    if payload.type == "article":
        if not titre:
            raise HTTPException(status_code=422, detail="Un article doit avoir un titre.")
        if payload.photos_supplementaires:
            raise HTTPException(status_code=422, detail="Un article n'a pas de photos supplémentaires.")
        image_couverture_url = payload.image_couverture_url or None

    elif payload.type == "reflexion":
        titre = None
        image_couverture_url = None
        if payload.photos_supplementaires:
            raise HTTPException(status_code=422, detail="Une réflexion ne contient pas de photo.")

    else:  # histoire
        if not titre:
            raise HTTPException(status_code=422, detail="Une histoire doit avoir un titre.")
        if not payload.image_couverture_url:
            raise HTTPException(status_code=422, detail="Une histoire doit avoir une photo de couverture.")
        if len(payload.photos_supplementaires) > MAX_PHOTOS_SUPPLEMENTAIRES:
            raise HTTPException(
                status_code=422,
                detail=f"Maximum {MAX_PHOTOS_SUPPLEMENTAIRES} photos supplémentaires en plus de la couverture.",
            )
        image_couverture_url = payload.image_couverture_url

    try:
        res = (
            supabase.table("posts")
            .insert(
                {
                    "user_id": utilisateur.id,
                    "type": payload.type,
                    "titre": titre,
                    "contenu": contenu,
                    "image_couverture_url": image_couverture_url,
                    "photos_supplementaires": payload.photos_supplementaires,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création post type={payload.type}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de publier pour le moment.")

    if not res.data:
        raise HTTPException(status_code=500, detail="La publication n'a pas pu être créée (erreur technique).")

    ligne = res.data[0]

    journaliser(
        action="post.publie",
        user_id=utilisateur.id,
        cible_type="post",
        cible_id=str(ligne["id"]),
        details={"type": payload.type, "titre": titre},
        request=request,
    )

    profils = _resoudre_profils([utilisateur.id])
    profil = profils.get(utilisateur.id, {})

    return Post(
        id=ligne["id"],
        user_id=ligne["user_id"],
        nom_affiche=profil.get("nom_affiche"),
        avatar_url=profil.get("avatar_url"),
        type=ligne["type"],
        titre=ligne.get("titre"),
        contenu=ligne["contenu"],
        image_couverture_url=ligne.get("image_couverture_url"),
        photos_supplementaires=ligne.get("photos_supplementaires") or [],
        created_at=ligne.get("created_at"),
    )


@router.get("", response_model=List[Post])
def lister_posts(
    type: str = Query(...),
    user_id: Optional[str] = Query(default=None),
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
):
    """
    Public, aucune auth. `type` obligatoire (les 3 onglets de l'accueil
    et du profil créateur appellent chacun ce endpoint séparément, jamais
    un mélange des 3 -- voir docstring du module) ; `user_id` optionnel
    pour filtrer sur un seul créateur (profil `/u/[id]`).
    """
    if type not in TYPES_VALIDES:
        raise HTTPException(status_code=422, detail="Type de publication invalide.")

    debut = (page - 1) * limite
    fin = debut + limite - 1
    try:
        requete = (
            supabase.table("posts")
            .select("id, user_id, type, titre, contenu, image_couverture_url, photos_supplementaires, created_at")
            .eq("type", type)
        )
        if user_id:
            requete = requete.eq("user_id", user_id)
        res = requete.order("created_at", desc=True).range(debut, fin).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste posts type={type}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les publications pour le moment.")

    lignes = res.data or []
    profils = _resoudre_profils([l["user_id"] for l in lignes])

    return [
        Post(
            id=l["id"],
            user_id=l["user_id"],
            nom_affiche=profils.get(l["user_id"], {}).get("nom_affiche"),
            avatar_url=profils.get(l["user_id"], {}).get("avatar_url"),
            type=l["type"],
            titre=l.get("titre"),
            contenu=l["contenu"],
            image_couverture_url=l.get("image_couverture_url"),
            photos_supplementaires=l.get("photos_supplementaires") or [],
            created_at=l.get("created_at"),
        )
        for l in lignes
    ]


@router.delete("/{post_id}", status_code=204)
def supprimer_post(post_id: int, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Sert notamment à "Supprimer une histoire" dans la zone de danger de
    Mon espace, mais générique pour les 3 types (même logique de
    propriété partout).
    """
    try:
        res = supabase.table("posts").select("user_id, type").eq("id", post_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture post {post_id} avant suppression) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer cette publication pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Publication introuvable.")
    if res.data["user_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cette publication ne t'appartient pas.")

    try:
        supabase.table("posts").delete().eq("id", post_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (suppression post {post_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer cette publication pour le moment.")

    journaliser(
        action="post.supprime",
        user_id=utilisateur.id,
        cible_type="post",
        cible_id=str(post_id),
        details={"type": res.data.get("type")},
        request=request,
    )
