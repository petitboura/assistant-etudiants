"""
Mises à jour publiées par un créateur sur un agent (demande Bourama,
2026-07-15) : champ "Mise à jour" dans "Modifier agent" pour raconter ce
qui a changé, affiché avec date sur la page publique de l'agent, avec
like/commentaire/partage (le partage réutilise components/BoutonPartager.tsx
côté frontend, rien à faire ici). Une notification est envoyée à tout
utilisateur ayant déjà échangé au moins une fois avec l'agent (même s'il
n'a envoyé qu'un seul message) -- gérée par un trigger Postgres
(`notifier_nouvelle_maj_agent`, voir la migration
`pivot_social_mises_a_jour_agent`), ce fichier ne fait qu'insérer la ligne
dans `agent_updates`, jamais les notifications elles-mêmes (même
convention que api/notifications.py).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, utilisateur_optionnel, supabase
from api.journal import journaliser

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents/{agent_id}/updates", tags=["agent_updates"])


def _verifier_proprietaire(agent_id: str, user_id: str):
    """Lève 404/403 si l'agent n'existe pas ou n'appartient pas à user_id."""
    try:
        res = (
            supabase.table("agents")
            .select("owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} pour mise à jour) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de vérifier cet agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != user_id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")


class MiseAJourCreee(BaseModel):
    titre: str
    contenu: str


class MiseAJour(BaseModel):
    id: int
    agent_id: str
    titre: str
    contenu: str
    created_at: Optional[str] = None
    total_likes: int = 0
    total_commentaires: int = 0
    jaime: bool = False


@router.post("", response_model=MiseAJour, status_code=201)
def publier_mise_a_jour(
    agent_id: str, payload: MiseAJourCreee, request: Request, utilisateur=Depends(utilisateur_courant)
):
    """
    Publie une mise à jour (propriétaire de l'agent uniquement). L'insertion
    déclenche le trigger Postgres qui notifie tout le monde ayant déjà
    utilisé l'agent -- rien à faire ici pour ça, voir docstring du module.
    """
    _verifier_proprietaire(agent_id, utilisateur.id)

    titre = payload.titre.strip()
    contenu = payload.contenu.strip()
    if not titre:
        raise HTTPException(status_code=422, detail="Le titre ne peut pas être vide.")
    if not contenu:
        raise HTTPException(status_code=422, detail="Le contenu ne peut pas être vide.")

    try:
        res = (
            supabase.table("agent_updates")
            .insert({"agent_id": agent_id, "user_id": utilisateur.id, "titre": titre, "contenu": contenu})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion mise à jour agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de publier la mise à jour pour le moment.")

    if not res.data:
        raise HTTPException(status_code=500, detail="La mise à jour n'a pas pu être créée (erreur technique).")

    ligne = res.data[0]

    journaliser(
        action="update.publie",
        user_id=utilisateur.id,
        cible_type="agent_update",
        cible_id=str(ligne["id"]),
        details={"agent_id": agent_id, "titre": titre},
        request=request,
    )

    return MiseAJour(
        id=ligne["id"],
        agent_id=ligne["agent_id"],
        titre=ligne["titre"],
        contenu=ligne["contenu"],
        created_at=ligne.get("created_at"),
        total_likes=0,
        total_commentaires=0,
        jaime=False,
    )


@router.get("", response_model=List[MiseAJour])
def lister_mises_a_jour(
    agent_id: str,
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
    utilisateur=Depends(utilisateur_optionnel),
):
    """
    Liste publique, plus récentes d'abord. `jaime` reflète l'utilisateur
    connecté s'il y en a un (utilisateur_optionnel, jamais de 401 ici,
    même pattern que GET /api/profiles/{user_id}).
    """
    debut = (page - 1) * limite
    fin = debut + limite - 1
    try:
        res = (
            supabase.table("agent_updates")
            .select("id, agent_id, titre, contenu, created_at")
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
            .range(debut, fin)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste mises à jour agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les mises à jour pour le moment.")

    lignes = res.data or []
    if not lignes:
        return []

    ids = [l["id"] for l in lignes]

    # Compte des likes/commentaires par mise à jour en 2 requêtes groupées
    # (pas une par ligne) -- même souci de performance que
    # api/agents.py:lister_commentaires.
    likes_par_id: dict = {}
    try:
        likes_res = supabase.table("agent_update_likes").select("update_id").in_("update_id", ids).execute()
        for l in likes_res.data or []:
            likes_par_id[l["update_id"]] = likes_par_id.get(l["update_id"], 0) + 1
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage likes mises à jour agent={agent_id}) : {e}")

    commentaires_par_id: dict = {}
    try:
        com_res = (
            supabase.table("agent_update_comments").select("update_id").in_("update_id", ids).execute()
        )
        for c in com_res.data or []:
            commentaires_par_id[c["update_id"]] = commentaires_par_id.get(c["update_id"], 0) + 1
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage commentaires mises à jour agent={agent_id}) : {e}")

    mes_likes = set()
    if utilisateur:
        try:
            mes_likes_res = (
                supabase.table("agent_update_likes")
                .select("update_id")
                .in_("update_id", ids)
                .eq("user_id", utilisateur.id)
                .execute()
            )
            mes_likes = {l["update_id"] for l in (mes_likes_res.data or [])}
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture mes likes, user={utilisateur.id}) : {e}")

    return [
        MiseAJour(
            id=l["id"],
            agent_id=l["agent_id"],
            titre=l["titre"],
            contenu=l["contenu"],
            created_at=l.get("created_at"),
            total_likes=likes_par_id.get(l["id"], 0),
            total_commentaires=commentaires_par_id.get(l["id"], 0),
            jaime=l["id"] in mes_likes,
        )
        for l in lignes
    ]


class LikeReponse(BaseModel):
    jaime: bool
    total_likes: int


@router.post("/{update_id}/like", response_model=LikeReponse)
def basculer_like(agent_id: str, update_id: int, utilisateur=Depends(utilisateur_courant)):
    """
    Toggle (pas d'endpoint like/unlike séparé) : si la ligne existe déjà
    pour (update_id, user_id), on la supprime, sinon on l'insère --
    contrainte de clé primaire composite empêche tout doublon.
    """
    try:
        existe = (
            supabase.table("agent_update_likes")
            .select("update_id")
            .eq("update_id", update_id)
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification like update={update_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le like pour le moment.")

    try:
        if existe and existe.data:
            supabase.table("agent_update_likes").delete().eq("update_id", update_id).eq(
                "user_id", utilisateur.id
            ).execute()
            jaime = False
        else:
            supabase.table("agent_update_likes").insert(
                {"update_id": update_id, "user_id": utilisateur.id}
            ).execute()
            jaime = True
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (toggle like update={update_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le like pour le moment.")

    try:
        total_res = (
            supabase.table("agent_update_likes")
            .select("update_id", count="exact")
            .eq("update_id", update_id)
            .execute()
        )
        total = total_res.count or 0
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (comptage likes update={update_id}) : {e}")
        total = 0

    return LikeReponse(jaime=jaime, total_likes=total)


class CommentaireMajCree(BaseModel):
    contenu: str


class CommentaireMaj(BaseModel):
    id: int
    update_id: int
    user_id: str
    nom_affiche: Optional[str] = None
    contenu: str
    created_at: Optional[str] = None


@router.get("/{update_id}/comments", response_model=List[CommentaireMaj])
def lister_commentaires_maj(
    agent_id: str, update_id: int, page: int = Query(1, ge=1), limite: int = Query(20, ge=1, le=50)
):
    debut = (page - 1) * limite
    fin = debut + limite - 1
    try:
        res = (
            supabase.table("agent_update_comments")
            .select("id, update_id, user_id, contenu, created_at")
            .eq("update_id", update_id)
            .order("created_at", desc=True)
            .range(debut, fin)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (commentaires update={update_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les commentaires pour le moment.")

    lignes = res.data or []
    noms_par_user_id: dict = {}
    ids_uniques = list({l["user_id"] for l in lignes})
    if ids_uniques:
        try:
            profils_res = (
                supabase.table("profiles").select("user_id, nom_affiche").in_("user_id", ids_uniques).execute()
            )
            for p in profils_res.data or []:
                if p.get("nom_affiche"):
                    noms_par_user_id[p["user_id"]] = p["nom_affiche"]
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture noms affichés, update={update_id}) : {e}")

    return [
        CommentaireMaj(
            id=l["id"],
            update_id=l["update_id"],
            user_id=l["user_id"],
            nom_affiche=noms_par_user_id.get(l["user_id"]),
            contenu=l["contenu"],
            created_at=l.get("created_at"),
        )
        for l in lignes
    ]


@router.post("/{update_id}/comments", response_model=CommentaireMaj, status_code=201)
def creer_commentaire_maj(
    agent_id: str, update_id: int, payload: CommentaireMajCree, utilisateur=Depends(utilisateur_courant)
):
    contenu = payload.contenu.strip()
    if not contenu:
        raise HTTPException(status_code=422, detail="Le commentaire ne peut pas être vide.")

    try:
        res = (
            supabase.table("agent_update_comments")
            .insert({"update_id": update_id, "user_id": utilisateur.id, "contenu": contenu})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion commentaire update={update_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le commentaire pour le moment.")

    if not res.data:
        raise HTTPException(status_code=500, detail="Le commentaire n'a pas pu être créé (erreur technique).")

    ligne = res.data[0]
    nom_affiche = None
    try:
        profil = (
            supabase.table("profiles").select("nom_affiche").eq("user_id", utilisateur.id).maybe_single().execute()
        )
        if profil and profil.data:
            nom_affiche = profil.data.get("nom_affiche") or None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture nom_affiche commentaire maj, user={utilisateur.id}) : {e}")

    return CommentaireMaj(
        id=ligne["id"],
        update_id=ligne["update_id"],
        user_id=ligne["user_id"],
        nom_affiche=nom_affiche,
        contenu=ligne["contenu"],
        created_at=ligne.get("created_at"),
    )
