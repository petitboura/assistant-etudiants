"""
Notifications (pivot social, 2026-07-15, Bourama : "icone notification
juste à côté de mon espace"). Les LIGNES sont créées par des triggers
Postgres (voir migration notifications_table_et_triggers) sur follows /
agent_comments / agent_ratings -- ce fichier ne fait que LIRE et marquer
comme lues, jamais insérer directement (une notification créée ici sans
passer par l'événement source serait incohérente avec ce que les
triggers créent pour les autres chemins).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationItem(BaseModel):
    id: int
    type: str  # "follow" | "comment" | "rating" | "categorie_manquante" | "agent_update"
    lu: bool
    created_at: Optional[str] = None
    # Nullable depuis le 2026-07-15 (Bourama : système de catégories) :
    # "categorie_manquante" est une notif système, sans acteur humain
    # (personne n'a "agi" sur toi, contrairement à follow/comment/rating).
    acteur_id: Optional[str] = None
    acteur_nom: str = ""
    acteur_avatar_url: Optional[str] = None
    agent_id: Optional[str] = None
    agent_nom: Optional[str] = None
    agent_icone: Optional[str] = None
    # Ajouté le 2026-07-15 pour le type "agent_update" (voir migration
    # pivot_social_mises_a_jour_agent) : permet au frontend de faire un
    # deep-link direct vers la mise à jour plutôt que juste l'agent.
    # Ajouté le 2026-07-21 (Bourama) : la notification "feedback" était
    # générique ("a noté X", texte hérité du fallback rating) et menait
    # sur la page publique de l'IA, sans jamais montrer le contenu du
    # retour -- alors que feedback_messages contient déjà tout (type,
    # commentaire, question/réponse partagées). Champs déduits, pas
    # stockés sur la ligne notifications elle-même (voir feedback_id).
    # PAS de nom d'utilisateur ici (demande explicite de Bourama) --
    # acteur_nom reste vide pour les notifications de type "feedback",
    # contrairement aux autres types où l'acteur est identifié.
    feedback_type: Optional[str] = None  # "positif" | "negatif"
    feedback_commentaire: Optional[str] = None
    feedback_contexte: bool = False  # question et/ou réponse partagée
    feedback_question: Optional[str] = None
    feedback_reponse: Optional[str] = None
    update_id: Optional[int] = None


class NotificationsReponse(BaseModel):
    notifications: List[NotificationItem]
    non_lues: int
    page: int
    limite: int
    total: int


@router.get("", response_model=NotificationsReponse)
def lister_notifications(
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Notifications du destinataire connecté, plus récentes d'abord.
    `non_lues` est TOUJOURS le total non lu (pas juste celles de cette
    page) -- c'est ce nombre qui alimente le badge de la cloche dans
    TopBar, indépendamment de la pagination.
    """
    debut = (page - 1) * limite
    fin = debut + limite - 1

    try:
        res = (
            supabase.table("notifications")
            .select("id, type, acteur_id, agent_id, feedback_id, update_id, lu, created_at", count="exact")
            .eq("user_id", utilisateur.id)
            .order("created_at", desc=True)
            .range(debut, fin)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture notifications user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les notifications pour le moment.")

    lignes = res.data or []
    total = res.count or 0

    try:
        non_lues_res = (
            supabase.table("notifications")
            .select("id", count="exact")
            .eq("user_id", utilisateur.id)
            .eq("lu", False)
            .execute()
        )
        non_lues = non_lues_res.count or 0
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage non lues user={utilisateur.id}) : {e}")
        non_lues = 0

    # Résolution groupée des noms/avatars acteurs et des agents concernés
    # (même pattern que api/agents.py:lister_commentaires -- une requête
    # par table, pas une par notification).
    acteurs_par_id: dict = {}
    ids_acteurs = list({l["acteur_id"] for l in lignes if l.get("acteur_id")})
    if ids_acteurs:
        try:
            profils_res = (
                supabase.table("profiles")
                .select("user_id, nom_affiche, avatar_url")
                .in_("user_id", ids_acteurs)
                .execute()
            )
            for p in profils_res.data or []:
                acteurs_par_id[p["user_id"]] = p
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture profils acteurs notifications) : {e}")

    agents_par_id: dict = {}
    ids_agents = list({l["agent_id"] for l in lignes if l.get("agent_id")})
    if ids_agents:
        try:
            agents_res = (
                supabase.table("agents")
                .select("id, nom, ui_config")
                .in_("id", ids_agents)
                .execute()
            )
            for a in agents_res.data or []:
                agents_par_id[a["id"]] = a
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture agents notifications) : {e}")

    # Résolution du contenu réel des feedbacks (2026-07-21) -- voir
    # commentaire sur feedback_type ci-dessus. Même pattern groupé que les
    # acteurs/agents : une requête pour tous les feedback_id de cette page,
    # pas une par notification.
    feedback_par_id: dict = {}
    ids_feedback = list({l["feedback_id"] for l in lignes if l.get("feedback_id")})
    if ids_feedback:
        try:
            fb_res = (
                supabase.table("feedback_messages")
                .select("id, type, commentaire, message_id, question_message_id, reponse_partagee, question_partagee")
                .in_("id", ids_feedback)
                .execute()
            )
            for f in fb_res.data or []:
                feedback_par_id[f["id"]] = f
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture feedback_messages notifications) : {e}")

    # Contenu de la question/réponse UNIQUEMENT si explicitement partagé
    # par l'étudiant (question_partagee / reponse_partagee) -- jamais
    # récupéré sinon, même si feedback_id existe.
    historique_par_id: dict = {}
    ids_historique = []
    for f in feedback_par_id.values():
        if f.get("question_partagee") and f.get("question_message_id"):
            ids_historique.append(f["question_message_id"])
        if f.get("reponse_partagee") and f.get("message_id"):
            ids_historique.append(f["message_id"])
    if ids_historique:
        try:
            hist_res = (
                supabase.table("historique_conversations")
                .select("id, content")
                .in_("id", list(set(ids_historique)))
                .execute()
            )
            for h in hist_res.data or []:
                historique_par_id[h["id"]] = h["content"]
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture historique pour feedback notifications) : {e}")

    notifications = []
    for l in lignes:
        profil_acteur = acteurs_par_id.get(l["acteur_id"]) or {}
        agent = agents_par_id.get(l.get("agent_id")) or {}
        est_feedback = l["type"] == "feedback"
        feedback = feedback_par_id.get(l.get("feedback_id")) or {} if est_feedback else {}
        feedback_contexte = bool(feedback.get("question_partagee") or feedback.get("reponse_partagee"))
        notifications.append(
            NotificationItem(
                id=l["id"],
                type=l["type"],
                lu=l["lu"],
                created_at=l.get("created_at"),
                acteur_id=l["acteur_id"],
                # Jamais de nom d'utilisateur sur une notification de
                # feedback (demande explicite de Bourama, 2026-07-21) --
                # contrairement aux autres types (follow/comment/rating)
                # où l'identité de l'acteur fait sens à afficher.
                acteur_nom="" if est_feedback else (profil_acteur.get("nom_affiche") or ""),
                acteur_avatar_url=None if est_feedback else profil_acteur.get("avatar_url"),
                agent_id=l.get("agent_id"),
                agent_nom=agent.get("nom"),
                agent_icone=(agent.get("ui_config") or {}).get("icone_page"),
                feedback_type=feedback.get("type"),
                feedback_commentaire=feedback.get("commentaire") or None,
                feedback_contexte=feedback_contexte,
                feedback_question=(
                    historique_par_id.get(feedback.get("question_message_id"))
                    if feedback.get("question_partagee")
                    else None
                ),
                feedback_reponse=(
                    historique_par_id.get(feedback.get("message_id"))
                    if feedback.get("reponse_partagee")
                    else None
                ),
                update_id=l.get("update_id"),
            )
        )

    return NotificationsReponse(
        notifications=notifications, non_lues=non_lues, page=page, limite=limite, total=total
    )


@router.post("/tout-lire", status_code=204)
def tout_marquer_lu(utilisateur=Depends(utilisateur_courant)):
    """Marque TOUTES les notifications du destinataire connecté comme lues."""
    try:
        supabase.table("notifications").update({"lu": True}).eq(
            "user_id", utilisateur.id
        ).eq("lu", False).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (tout marquer lu, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de marquer les notifications comme lues.")


@router.patch("/{notification_id}", status_code=204)
def marquer_lu(notification_id: int, utilisateur=Depends(utilisateur_courant)):
    """
    Marque UNE notification comme lue (clic sur une ligne précise).
    Scope par user_id en plus de l'id : un utilisateur ne doit pas
    pouvoir marquer comme lue une notification qui n'est pas la sienne,
    même en devinant un id valide.
    """
    try:
        supabase.table("notifications").update({"lu": True}).eq("id", notification_id).eq(
            "user_id", utilisateur.id
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer lu notif={notification_id}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de marquer cette notification comme lue.")
