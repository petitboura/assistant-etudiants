"""
Génération de documents/code/images déclenchée par un BOUTON explicite
côté frontend (par opposition à l'agent qui décide seul via le serveur
MCP -- voir core/serveur_mcp_generation.py). Même logique métier
(core/generation_*.py), deux points d'entrée différents, comme discuté
avec Bourama le 2026-07-20.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.generation_documents import generer_pdf_depuis_markdown
from core.generation_code import generer_zip_depuis_fichiers
from core.generation_images import generer_image, image_generation_disponible

router = APIRouter(prefix="/api/generation", tags=["generation"])


class DemandeDocument(BaseModel):
    titre: str
    contenu_markdown: str


class DemandeCode(BaseModel):
    nom_projet: str
    fichiers: dict[str, str]


class DemandeImage(BaseModel):
    prompt: str


class ReponseGeneration(BaseModel):
    url: str


@router.post("/document", response_model=ReponseGeneration)
def generer_document_route(demande: DemandeDocument, utilisateur=Depends(utilisateur_courant)):
    try:
        url = generer_pdf_depuis_markdown(demande.titre, demande.contenu_markdown)
    except Exception as e:
        logging.error(f"ERREUR génération document (utilisateur {utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Échec de la génération du document, réessaie.")
    return ReponseGeneration(url=url)


@router.post("/code", response_model=ReponseGeneration)
def generer_code_route(demande: DemandeCode, utilisateur=Depends(utilisateur_courant)):
    try:
        url = generer_zip_depuis_fichiers(demande.nom_projet, demande.fichiers)
    except Exception as e:
        logging.error(f"ERREUR génération code (utilisateur {utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Échec de la génération de l'archive, réessaie.")
    return ReponseGeneration(url=url)


@router.post("/image", response_model=ReponseGeneration)
def generer_image_route(demande: DemandeImage, utilisateur=Depends(utilisateur_courant)):
    if not image_generation_disponible():
        # 503 (pas 500) : signale explicitement au frontend "fonctionnalité
        # pas encore activée", à distinguer d'une vraie panne -- voir
        # BarreDeSaisie.tsx pour l'affichage "bientôt disponible" attendu.
        raise HTTPException(
            status_code=503,
            detail="La génération d'image n'est pas encore activée sur cette plateforme.",
        )
    try:
        url = generer_image(demande.prompt)
    except Exception as e:
        logging.error(f"ERREUR génération image (utilisateur {utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Échec de la génération de l'image, réessaie.")
    return ReponseGeneration(url=url)
