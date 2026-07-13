"""
Étape ajoutée le 2026-07-13 (Bourama : "conversation récente par membre de
la plateforme qui se conserve pour chaque agent utilisée, dans le tableau
de bord, à gauche comme toute IA en fait").

Lit `historique_conversations` (voir la migration du même nom) : une table
PERMANENTE, jamais purgée, distincte de `conversations` (mémoire de
travail de l'IA, résumée puis supprimée -- voir core/main.py). Ce fichier
ne fait qu'AFFICHER l'historique ; il n'écrit jamais dedans (l'écriture se
fait uniquement depuis core/main.py, au moment de chaque échange).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase

router = APIRouter(prefix="/api/historique", tags=["historique"])


class ConversationResume(BaseModel):
    agent_id: str
    agent_nom: str
    agent_icone: str = "🤖"
    dernier_message: str
    dernier_message_role: str
    derniere_activite: str


@router.get("", response_model=List[ConversationResume])
def lister_conversations(utilisateur=Depends(utilisateur_courant)):
    """
    Liste des agents avec qui CET utilisateur a déjà échangé, le plus
    récemment actif en premier -- pour la barre latérale façon ChatGPT
    demandée par Bourama. Un agent = une "conversation" ; le détail
    message par message est sur GET /api/historique/{agent_id}.

    Pas de pagination pour l'instant : le nombre d'agents avec qui un même
    utilisateur discute reste naturellement borné (contrairement au feed
    public), donc pas de risque de volume immédiat -- à revisiter si ça
    devient un problème réel (même remarque que pour le feed, voir
    PIVOT_SOCIAL.md).
    """
    try:
        lignes = (
            supabase.table("historique_conversations")
            .select("agent_id, role, content, created_at")
            .eq("user_id", utilisateur.id)
            .order("created_at", desc=True)
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lister_conversations, user_id={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger l'historique.")

    # Un seul aller-retour supabase pour tous les messages (déjà trié du
    # plus récent au plus ancien), puis on garde juste la PREMIÈRE ligne
    # rencontrée par agent_id -- c'est mécaniquement la plus récente,
    # grâce au tri ci-dessus. Évite une requête groupée par agent plus
    # coûteuse pour un gain minime ici.
    resume_par_agent = {}
    for ligne in lignes:
        aid = ligne["agent_id"]
        if aid not in resume_par_agent:
            resume_par_agent[aid] = ligne

    if not resume_par_agent:
        return []

    try:
        agents_res = (
            supabase.table("agents")
            .select("id, nom, ui_config")
            .in_("id", list(resume_par_agent.keys()))
            .execute()
        )
        agents_par_id = {a["id"]: a for a in (agents_res.data or [])}
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lister_conversations, jointure agents) : {e}")
        agents_par_id = {}

    resultat = []
    for agent_id, ligne in resume_par_agent.items():
        agent = agents_par_id.get(agent_id)
        # Agent supprimé depuis (actif=false n'est PAS filtré ici : voir
        # docstring — on veut quand même montrer l'historique d'un agent
        # désactivé, juste pas un agent qui n'existe plus du tout en base)
        # mais un id qui ne matche plus aucune ligne dans `agents` est
        # silencieusement ignoré plutôt que de planter toute la liste.
        if not agent:
            continue
        resultat.append(
            ConversationResume(
                agent_id=agent_id,
                agent_nom=agent["nom"],
                agent_icone=(agent.get("ui_config") or {}).get("icone_page", "🤖"),
                dernier_message=ligne["content"],
                dernier_message_role=ligne["role"],
                derniere_activite=ligne["created_at"],
            )
        )

    resultat.sort(key=lambda r: r.derniere_activite, reverse=True)
    return resultat


class MessageHistorique(BaseModel):
    role: str
    content: str
    created_at: str


@router.get("/{agent_id}", response_model=List[MessageHistorique])
def obtenir_historique_agent(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Historique complet (jamais purgé) des échanges entre CET utilisateur
    et CET agent, du plus ancien au plus récent -- pour rouvrir/afficher
    une conversation passée en entier depuis la barre latérale.

    Pas de vérification "cet agent existe/est actif" ici : filtrer par
    user_id suffit à garantir qu'on ne lit jamais l'historique de
    quelqu'un d'autre (aucune donnée du payload/de l'URL n'influence QUEL
    user_id est utilisé, uniquement le token vérifié par
    utilisateur_courant).
    """
    try:
        lignes = (
            supabase.table("historique_conversations")
            .select("role, content, created_at")
            .eq("user_id", utilisateur.id)
            .eq("agent_id", agent_id)
            .order("created_at")
            .execute()
        ).data or []
    except Exception as e:
        logging.error(
            f"ERREUR SUPABASE (obtenir_historique_agent, user_id={utilisateur.id}, "
            f"agent_id={agent_id}) : {e}"
        )
        raise HTTPException(status_code=500, detail="Impossible de charger l'historique.")

    return [MessageHistorique(**ligne) for ligne in lignes]
