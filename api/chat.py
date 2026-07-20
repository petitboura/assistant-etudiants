"""
Route de chat en streaming pour le frontend Next.js (djiguign--ai).

Chaînon manquant identifié pendant la migration Streamlit -> Next.js (voir
MIGRATION_CHAT_VERS_NEXTJS.md, section 0) : jusqu'ici, la fonction chat()
(core/main.py) n'était appelée qu'en interne par chat.py (Streamlit), en
process. Cette route l'expose en HTTP, via Server-Sent Events (SSE), pour
que la nouvelle page de chat React puisse lui parler à distance.

La logique IA elle-même (chat(), dans core/main.py) n'est PAS réécrite ici,
seulement branchée à un vrai endpoint HTTP -- même prompt système, mêmes
outils MCP, même cascade de modèles qu'avant.
"""

import json
import logging
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Literal

from api.auth import utilisateur_optionnel
from main import chat as chat_generateur  # core/main.py:chat()

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class MessageHistorique(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Localisation(BaseModel):
    latitude: float
    longitude: float


class RepriseConfirmation(BaseModel):
    etat_reprise: dict
    approuve: bool


class EnvoyerMessagePayload(BaseModel):
    # message/agent_id optionnels : sur un appel de reprise (après
    # confirmation_requise), seul `reprise` est fourni -- chat_generateur
    # les ignore dans ce cas (voir core/main.py:chat()).
    message: Optional[str] = None
    agent_id: Optional[str] = None
    historique: List[MessageHistorique] = []
    conversation_id: Optional[str] = None
    # Barre de saisie migrée (MIGRATION_CHAT_VERS_NEXTJS.md, section 3.3) :
    # sélecteur Courte/Moyenne/Longue, modifiable à chaque message.
    longueur_reponse: Literal["courte", "moyenne", "longue"] = "moyenne"
    # Image jointe au message (URL publique renvoyée par
    # POST /api/uploads/image-chat, voir uploads.py). Quand présente,
    # core/main.py:chat() route directement vers Gemini (seul modèle
    # multimodal de la cascade) au lieu du cascade Groq habituel — voir
    # le commentaire au-dessus de la branche image_url dans chat().
    image_url: Optional[str] = None
    # Position GPS transmise explicitement par l'étudiant via un bouton
    # dédié (jamais capturée automatiquement) -- voir core/main.py:chat(),
    # paramètre localisation, injecté en contexte de prompt système.
    localisation: Optional[Localisation] = None
    # Fuseau IANA du navigateur (Intl.DateTimeFormat().resolvedOptions().timeZone),
    # PAS une valeur choisie côté serveur -- voir core/main.py:chat().
    fuseau_horaire: Optional[str] = None
    # Frames JPEG en base64, extraites d'une vidéo uploadée (voir
    # api/uploads.py:uploader_video_chat). Combinable avec image_url mais
    # rarement les deux en même temps en pratique.
    images_base64: Optional[List[str]] = None
    # Ajouté (2026-07-20) pour exposer le chemin de reprise de chat() --
    # jusqu'ici accessible seulement en appel Python interne (chat.py
    # Streamlit), jamais via cette route HTTP. Voir StatutOutil.tsx /
    # ChatIA.tsx côté djiguign--ai pour le flux de confirmation d'outil.
    reprise: Optional[RepriseConfirmation] = None


def _evenements_sse(payload: EnvoyerMessagePayload, user_id: Optional[str]):
    """
    Convertit le générateur Python de chat() en flux SSE (`data: {...}\n\n`
    par événement), le format que `fetch` + `ReadableStream` côté Next.js
    sait consommer nativement sans dépendance supplémentaire.

    Ne change PAS la structure des événements produits par chat() (voir sa
    docstring) -- on les sérialise tels quels, un JSON par ligne `data:`.
    """
    try:
        if payload.reprise is not None:
            generateur = chat_generateur(
                reprise={
                    "etat_reprise": payload.reprise.etat_reprise,
                    "approuve": payload.reprise.approuve,
                }
            )
        else:
            generateur = chat_generateur(
                message_utilisateur=payload.message,
                historique=[m.model_dump() for m in payload.historique],
                user_id=user_id,
                agent_id=payload.agent_id,
                conversation_id=payload.conversation_id,
                longueur_reponse=payload.longueur_reponse,
                image_url=payload.image_url,
                localisation=payload.localisation.model_dump() if payload.localisation else None,
                fuseau_horaire=payload.fuseau_horaire,
                images_base64=payload.images_base64,
            )
        for evenement in generateur:
            yield f"data: {json.dumps(evenement)}\n\n"
    except Exception as e:
        logging.error(f"ERREUR chat() en streaming (agent_id={payload.agent_id}) : {e}")
        yield f"data: {json.dumps({'type': 'reponse', 'texte': 'Une erreur est survenue, réessaie dans un instant.'})}\n\n"
    # Signal de fin explicite : côté Next.js, permet de savoir que le flux
    # est terminé sans dépendre uniquement de la fermeture de connexion.
    yield "data: [DONE]\n\n"


@router.post("")
def envoyer_message(payload: EnvoyerMessagePayload, utilisateur=Depends(utilisateur_optionnel)):
    """
    Chat accessible aux visiteurs non connectés (utilisateur_optionnel),
    comme sur chat.py -- voir SEUIL_VISITEUR_NON_CONNECTE côté ancien
    frontend Streamlit ; la même limite devra être réimplémentée côté
    Next.js (comptage local, pas de dépendance à cette route pour ça).

    user_id=None si non connecté : chat() gère déjà ce cas (pas de
    mémoire long-terme persistée, pas d'événement "meta" -- voir sa
    docstring), donc rien de spécial à faire ici.
    """
    user_id = utilisateur.id if utilisateur else None
    return StreamingResponse(
        _evenements_sse(payload, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # évite le buffering côté proxy (Railway/nginx)
        },
    )
