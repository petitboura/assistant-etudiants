"""
Étape 1 du plan (voir api/PLAN.md) : POST /api/agents.

Équivalent du formulaire faces/vues/creer_agent.py, SANS l'upload de PDF
(volontairement laissé à l'Étape 2 : POST /api/agents/{id}/documents —
un fichier ne se transporte pas naturellement dans le même corps JSON
qu'un formulaire structuré, et creer_agent.py traite déjà ces deux
aspects comme deux blocs largement indépendants).

Réutilise telle quelle la logique déjà partagée avec le formulaire
Streamlit (core/creation_agent.py), pas de duplication (voir décision
d'architecture #3 dans api/PLAN.md).
"""

import os
import sys
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, supabase, get_secret

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "indexers"))
from creation_agent import generer_id_depuis_nom, extraire_id_notion, composer_system_prompt  # noqa: E402
from index_documents import indexer_texte  # noqa: E402

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class LigneComportement(BaseModel):
    type_requete: str = ""
    comportement: str = ""


class UiConfig(BaseModel):
    """
    Depuis le pivot social (2026-07-11, voir PIVOT_SOCIAL.md, section
    "Ce qui change") : le thème visuel par agent est supprimé, un seul
    thème fixe s'applique à toute la plateforme. Seul icone_page reste
    personnalisable ici — tous les anciens champs (couleurs, police,
    rayon des bulles, CSS avancé, style de titre multicolore...) sont
    retirés, pas juste ignorés, pour ne pas garder de code mort côté API.
    Cible finale de `agents.ui_config` en base (le nettoyage de la
    colonne elle-même, avec les agents déjà créés, reste une étape à
    part, voir PIVOT_SOCIAL.md Étape B).
    """
    icone_page: str = "🤖"


class CreerAgentPayload(BaseModel):
    nom: str
    ton: str  # "Tutoiement (tu)" | "Vouvoiement (vous)"
    posture_generale: str = ""
    limites_globales: str = ""
    comportements: List[LigneComportement] = Field(default_factory=list)
    outils_choisis: List[str] = Field(default_factory=list)
    type_connaissance: str
    description_connaissance: str = ""
    lien_notion: Optional[str] = None
    texte_libre: str = ""
    ui_config: UiConfig = Field(default_factory=UiConfig)
    # Nouveau flow de création (pivot social) : image de vitrine et
    # description publique de la page agent, distinctes de
    # description_connaissance qui reste un usage interne au RAG.
    image_vitrine_url: Optional[str] = None
    description: str = ""


class AgentCree(BaseModel):
    id: str
    nom: str
    lien: Optional[str] = None


@router.post("", response_model=AgentCree, status_code=201)
def creer_agent(payload: CreerAgentPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.nom.strip():
        raise HTTPException(status_code=422, detail="Le nom de l'agent est obligatoire.")
    if not payload.posture_generale.strip() and not payload.limites_globales.strip():
        raise HTTPException(
            status_code=422,
            detail="Remplis au moins la posture générale ou les limites globales.",
        )

    agent_id = generer_id_depuis_nom(payload.nom)

    try:
        existe_deja = (
            supabase.table("agents").select("id").eq("id", agent_id).maybe_single().execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification unicité agent_id={agent_id}) : {e}")
        existe_deja = None

    if existe_deja and existe_deja.data:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Un agent existe déjà avec un nom trop proche (id généré: {agent_id}). "
                "Choisis un nom légèrement différent."
            ),
        )

    lignes_comportement = [(l.type_requete, l.comportement) for l in payload.comportements]
    system_prompt = composer_system_prompt(
        payload.ton, payload.posture_generale, payload.limites_globales,
        lignes_comportement, payload.type_connaissance, payload.description_connaissance,
    )
    notion_page_id = extraire_id_notion(payload.lien_notion)

    # Depuis le pivot social : plus de personnalisation de thème par agent,
    # seuls titre/icône/emoji dérivés du nom et de l'icône restent écrits
    # dans ui_config. faces/vues/chat.py retombe sur UI_CONFIG_PAR_DEFAUT
    # pour tout le reste (couleurs, police, rendu_visuel, etc.), ce qui
    # est le comportement voulu : un seul thème fixe pour la plateforme.
    ui = payload.ui_config
    ui_config_dict = {
        "titre_page": payload.nom.strip(),
        "icone_page": ui.icone_page.strip() or "🤖",
        "titre_accueil": f"{ui.icone_page.strip()} {payload.nom.strip()}",
        "emoji_reponse": ui.icone_page.strip(),
    }

    knowledge_source = {
        "type": payload.type_connaissance,
        "description": payload.description_connaissance.strip(),
        # Conservé tel quel (pas seulement indexé), même choix que
        # creer_agent.py, pour pouvoir être réaffiché/modifié plus tard.
        "texte_libre": payload.texte_libre.strip(),
    }

    nouvelle_ligne = {
        "id": agent_id,
        "nom": payload.nom.strip(),
        "system_prompt": system_prompt,
        "ui_config": ui_config_dict,
        "knowledge_source": knowledge_source,
        "tools_enabled": payload.outils_choisis,
        "owner_id": utilisateur.id,
        # Colonnes ajoutées par la migration pivot_social_etape_b_tables
        # (voir PIVOT_SOCIAL.md, Étape B) : vitrine publique de l'agent,
        # distincte de knowledge_source.description (usage RAG interne).
        "image_vitrine_url": payload.image_vitrine_url,
        "description": payload.description.strip(),
    }
    if notion_page_id:
        nouvelle_ligne["notion_page_id"] = notion_page_id

    try:
        supabase.table("agents").insert(nouvelle_ligne).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion agent {agent_id}) : {e}")
        raise HTTPException(
            status_code=500,
            detail="Impossible de créer l'agent (erreur technique). Réessaie dans un instant.",
        )

    # Indexation du texte libre : best-effort, n'annule jamais la création
    # de l'agent (même choix que creer_agent.py) si elle échoue.
    if payload.texte_libre.strip():
        try:
            indexer_texte(agent_id, "texte-libre", payload.texte_libre.strip())
        except Exception as e:
            logging.error(f"ERREUR indexation texte libre (agent_id={agent_id}) : {e}")

    url_base = get_secret("URL_RETOUR_APP")
    lien = f"{url_base.rstrip('/')}/?agent={agent_id}" if url_base else None
    if not url_base:
        logging.error("URL_RETOUR_APP absent : impossible de construire le lien complet de l'agent.")

    return AgentCree(id=agent_id, nom=payload.nom.strip(), lien=lien)


class AgentDetailPublic(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"
    image_vitrine_url: Optional[str] = None
    description: str = ""
    owner_id: str


@router.get("/{agent_id}", response_model=AgentDetailPublic)
def obtenir_agent_public(agent_id: str):
    """
    Détail public d'un agent, pour la page `/agent/[slug]` (voir
    PIVOT_SOCIAL.md, Étape C, Étape E). Public, aucune auth requise, comme
    `/api/feed`. `agent_id` sert de slug : pas de colonne `slug` dédiée
    sur `agents` (voir PIVOT_SOCIAL.md, changelog "Étape B terminée").

    `owner_id` est renvoyé pour permettre au frontend de lier vers le
    portfolio créateur (`/u/[slug]`, Étape E) une fois `GET
    /api/profiles/{slug}` construit ; pas encore de résolution
    profil <-> agent ici, volontairement, pour ne pas dupliquer une
    logique qui appartient à l'endpoint profils.

    404 si l'agent n'existe pas OU s'il est désactivé (`actif` is
    False) : une page publique ne doit pas exister pour un agent
    désactivé, même en connaissant son id directement. Convention "True
    par défaut" si `actif` est absent/NULL, identique à
    `faces/vues/chat.py:_agent_est_actif` et à `/api/feed`.
    """
    try:
        res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description, owner_id, actif")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent public {agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger cet agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne.get("actif") is False:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    return AgentDetailPublic(
        id=ligne["id"],
        nom=ligne["nom"],
        icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
        image_vitrine_url=ligne.get("image_vitrine_url"),
        description=ligne.get("description") or "",
        owner_id=ligne["owner_id"],
    )


class MettreAJourVitrinePayload(BaseModel):
    # Optional (pas absent = pas de valeur) volontairement, pour un PATCH
    # partiel : un champ omis (None) n'est pas touché, contrairement à une
    # chaîne vide envoyée explicitement, qui efface la valeur existante.
    image_vitrine_url: Optional[str] = None
    description: Optional[str] = None


@router.patch("/{agent_id}/vitrine", response_model=AgentDetailPublic)
def mettre_a_jour_vitrine(
    agent_id: str,
    payload: MettreAJourVitrinePayload,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Mise à jour de la vitrine publique d'un agent (image + description),
    depuis le dashboard "Mes agents" (voir PIVOT_SOCIAL.md, Étape F).

    Vérifie que `owner_id` du token correspond au propriétaire de l'agent
    (403 sinon) — même exigence que celle notée pour l'upload de
    documents à l'Étape 2 de `api/PLAN.md`, appliquée ici en premier
    puisque c'est le premier endpoint de modification (hors création) du
    pivot social.
    """
    try:
        res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description, owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant mise à jour vitrine) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de mettre à jour la vitrine pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    mise_a_jour = {}
    if payload.image_vitrine_url is not None:
        mise_a_jour["image_vitrine_url"] = payload.image_vitrine_url
    if payload.description is not None:
        mise_a_jour["description"] = payload.description.strip()

    if not mise_a_jour:
        raise HTTPException(
            status_code=422,
            detail="Rien à mettre à jour (image_vitrine_url et description sont absents).",
        )

    try:
        supabase.table("agents").update(mise_a_jour).eq("id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (mise à jour vitrine agent {agent_id}) : {e}")
        raise HTTPException(
            status_code=500,
            detail="Impossible de mettre à jour la vitrine (erreur technique). Réessaie dans un instant.",
        )

    ligne.update(mise_a_jour)
    return AgentDetailPublic(
        id=ligne["id"],
        nom=ligne["nom"],
        icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
        image_vitrine_url=ligne.get("image_vitrine_url"),
        description=ligne.get("description") or "",
        owner_id=ligne["owner_id"],
    )


class NoterAgentPayload(BaseModel):
    note: int


@router.post("/{agent_id}/rating", status_code=204)
def noter_agent(agent_id: str, payload: NoterAgentPayload, utilisateur=Depends(utilisateur_courant)):
    """
    Note un agent de 1 à 5 (table `agent_ratings`, voir PIVOT_SOCIAL.md :
    contrainte unique `(agent_id, user_id)` — un utilisateur note un agent
    une seule fois mais peut modifier sa note). Upsert plutôt qu'insert
    pour porter ce comportement directement, sans 409 + endpoint PATCH
    séparé pour le même geste côté frontend (contrairement à `/vitrine`,
    qui est une vraie modification d'un objet déjà possédé).

    Ne vérifie pas que l'agent existe avant d'insérer : la contrainte FK
    `agent_id` fera déjà échouer l'upsert proprement si l'agent n'existe
    pas, pas besoin de dupliquer cette vérification ici.
    """
    if not 1 <= payload.note <= 5:
        raise HTTPException(status_code=422, detail="La note doit être comprise entre 1 et 5.")

    try:
        supabase.table("agent_ratings").upsert(
            {"agent_id": agent_id, "user_id": utilisateur.id, "note": payload.note},
            on_conflict="agent_id,user_id",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (upsert note agent={agent_id}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer la note pour le moment.")


class NoteAgregee(BaseModel):
    moyenne: Optional[float] = None
    total: int = 0


@router.get("/{agent_id}/rating", response_model=NoteAgregee)
def obtenir_note_agent(agent_id: str):
    """
    Note moyenne publique d'un agent, pour l'affichage "note 1-5" sur
    `/agent/[slug]` (voir PIVOT_SOCIAL.md, tableau des pages du frontend).
    Public, aucune auth. `moyenne` reste `None` (pas 0) tant qu'aucune
    note n'existe, pour que le frontend distingue "pas encore noté" de
    "noté 0".
    """
    try:
        res = supabase.table("agent_ratings").select("note").eq("agent_id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture notes agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger la note pour le moment.")

    notes = [ligne["note"] for ligne in (res.data or [])]
    if not notes:
        return NoteAgregee(moyenne=None, total=0)
    return NoteAgregee(moyenne=round(sum(notes) / len(notes), 2), total=len(notes))


class CommentaireCree(BaseModel):
    contenu: str


class Commentaire(BaseModel):
    id: str
    agent_id: str
    user_id: str
    contenu: str
    created_at: Optional[str] = None


@router.post("/{agent_id}/comments", response_model=Commentaire, status_code=201)
def creer_commentaire(agent_id: str, payload: CommentaireCree, utilisateur=Depends(utilisateur_courant)):
    """
    Ajoute un commentaire sur un agent (table `agent_comments`, voir
    PIVOT_SOCIAL.md). Un commentaire par appel ; aucune limite de nombre
    par utilisateur pour l'instant, aucune modération demandée par
    Bourama à ce stade — à revoir si besoin plus tard.
    """
    contenu = payload.contenu.strip()
    if not contenu:
        raise HTTPException(status_code=422, detail="Le commentaire ne peut pas être vide.")

    try:
        res = (
            supabase.table("agent_comments")
            .insert({"agent_id": agent_id, "user_id": utilisateur.id, "contenu": contenu})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion commentaire agent={agent_id}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le commentaire pour le moment.")

    if not res.data:
        raise HTTPException(status_code=500, detail="Le commentaire n'a pas pu être créé (erreur technique).")

    ligne = res.data[0]
    return Commentaire(
        id=str(ligne["id"]),
        agent_id=ligne["agent_id"],
        user_id=ligne["user_id"],
        contenu=ligne["contenu"],
        created_at=ligne.get("created_at"),
    )


@router.get("/{agent_id}/comments", response_model=List[Commentaire])
def lister_commentaires(
    agent_id: str,
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
):
    """
    Liste paginée des commentaires d'un agent, plus récents d'abord.
    Public, aucune auth requise. Mêmes bornes de pagination que
    `/api/feed` (limite plafonnée à 50/page).
    """
    debut = (page - 1) * limite
    fin = debut + limite - 1
    try:
        res = (
            supabase.table("agent_comments")
            .select("id, agent_id, user_id, contenu, created_at")
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
            .range(debut, fin)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture commentaires agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les commentaires pour le moment.")

    return [
        Commentaire(
            id=str(ligne["id"]),
            agent_id=ligne["agent_id"],
            user_id=ligne["user_id"],
            contenu=ligne["contenu"],
            created_at=ligne.get("created_at"),
        )
        for ligne in (res.data or [])
    ]
