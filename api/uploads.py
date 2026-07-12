"""
Upload d'images (image de vitrine d'un agent, avatar de profil). Ajouté
le 2026-07-12 suite à un bug remonté par Bourama : les champs "URL image"
demandaient de coller un lien à la main, pas utilisable pour quelqu'un de
non-technique (voir PIVOT_SOCIAL.md, section Étape D). Remplacés côté
frontend par un vrai bouton d'upload (components/ChampImage.tsx), qui
passe par ce endpoint.

L'upload passe TOUJOURS par ici (service role key), jamais directement du
navigateur vers Supabase Storage : pas de policy RLS sur storage.objects,
cohérent avec le reste du projet (aucune table n'a de policy non plus,
tout passe par le service role côté Python — voir la note dans la
migration Supabase `pivot_social_etape_b_tables`).
"""

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api.auth import supabase, utilisateur_courant

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

BUCKET = "images-publiques"

TYPES_AUTORISES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

TAILLE_MAX_OCTETS = 5 * 1024 * 1024  # 5 Mo


@router.post("/image")
async def uploader_image(
    fichier: UploadFile = File(...),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Upload une image (jpeg/png/webp, 5 Mo max) dans le bucket public
    `images-publiques`, sous le chemin `{user_id}/{uuid}.{extension}` —
    un dossier par utilisateur, pas de collision possible entre deux
    personnes qui uploadent au même moment. Renvoie l'URL publique,
    directement utilisable comme `image_vitrine_url` ou `avatar_url`.
    """
    if fichier.content_type not in TYPES_AUTORISES:
        raise HTTPException(
            status_code=400,
            detail="Format non supporté (jpeg, png ou webp uniquement).",
        )

    contenu = await fichier.read()
    if len(contenu) > TAILLE_MAX_OCTETS:
        raise HTTPException(status_code=400, detail="Image trop lourde (5 Mo max).")
    if len(contenu) == 0:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    extension = TYPES_AUTORISES[fichier.content_type]
    chemin = f"{utilisateur.id}/{uuid.uuid4()}.{extension}"

    try:
        supabase.storage.from_(BUCKET).upload(
            chemin,
            contenu,
            {"content-type": fichier.content_type},
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload {chemin}) : {e}")
        raise HTTPException(status_code=500, detail="Échec de l'upload, réessaie.")

    url = supabase.storage.from_(BUCKET).get_public_url(chemin)
    return {"url": url}
