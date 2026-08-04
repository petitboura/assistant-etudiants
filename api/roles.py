"""
Hiérarchie de rôles (nous/établissement/enseignant/étudiant), ajoutée le
2026-08-04 (demande Bourama). Voir migrations/2026_08_04_roles_hierarchie.sql
pour le schéma et api/permissions_hierarchie.py pour les vérifications de
droits réutilisées par api/agents.py.

Rattachement choisi UNE FOIS par l'utilisateur à l'inscription (menu
déroulant, pas d'invitation -- décision Bourama), jamais modifiable
ensuite via cet endpoint (repli : à corriger à la main en base par
Bourama en cas d'erreur, cas rare).

Pas d'UI vitrine pour cette fonctionnalité (demande Bourama) : purement
fonctionnel, branché dans l'espace connecté de l'app.
"""

import logging
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from api.permissions_hierarchie import _lire_profil_role
from creation_agent import generer_id_depuis_nom
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/roles", tags=["roles"])

ROLES_VALIDES = ("etablissement", "enseignant", "etudiant")

# Prompts par défaut à la création automatique de l'IA d'un compte à rôle
# (2026-08-04) -- texte de départ modifiable ensuite comme n'importe quel
# agent (par son propriétaire, ou son superviseur selon la hiérarchie).
SYSTEM_PROMPT_PAR_DEFAUT = {
    "etablissement": "Tu es l'assistant IA de cet établissement. Sois clair, professionnel et utile.",
    "enseignant": "Tu es l'assistant IA de cet enseignant. Sois clair, pédagogique et utile.",
    "etudiant": "Tu es l'assistant IA de cet étudiant. Sois clair, pédagogique et bienveillant.",
}


def _nom_affiche_ou_repli(user_id: str) -> str:
    try:
        res = (
            supabase.table("profiles")
            .select("nom_affiche")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (nom_affiche {user_id}) : {e}")
        res = None
    return ((res.data or {}).get("nom_affiche") if res else None) or "Sans nom"


def _creer_agent_minimal(owner_id: str, role: str, nom_affiche: str) -> str:
    """
    Crée l'IA propre à un compte à rôle, avec un system_prompt de départ
    (voir SYSTEM_PROMPT_PAR_DEFAUT) -- INSERT direct plutôt que de passer
    par POST /api/agents (qui exige un formulaire complet : ton, posture,
    comportements...). Pas de matière/catégorie : ces colonnes sont
    NULLABLE et leur contrainte UNIQUE n'empêche pas plusieurs NULL
    (comportement standard Postgres), donc aucun conflit possible ici.
    """
    base = generer_id_depuis_nom(f"{role}-{nom_affiche}") or f"{role}-{owner_id[:8]}"
    agent_id = base
    suffixe = 0
    while True:
        try:
            existe = supabase.table("agents").select("id").eq("id", agent_id).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification unicité agent_id={agent_id}) : {e}")
            existe = None
        if not existe or not existe.data:
            break
        suffixe += 1
        agent_id = f"{base}-{suffixe}"

    ui_config = {
        "titre_page": nom_affiche,
        "icone_page": "🤖",
        "titre_accueil": f"🤖 {nom_affiche}",
        "sous_titre_accueil": f"IA de {nom_affiche}",
        "emoji_reponse": "🤖",
        "placeholder_saisie": "Pose ta question...",
    }
    try:
        supabase.table("agents").insert(
            {
                "id": agent_id,
                "nom": nom_affiche,
                "system_prompt": SYSTEM_PROMPT_PAR_DEFAUT[role],
                "ui_config": ui_config,
                "knowledge_source": {},
                "owner_id": owner_id,
                "description": f"IA de {nom_affiche}",
            }
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création agent auto rôle={role}, owner={owner_id}) : {e}")
        raise erreur_api(500, "AGENT_CREE_MAIS_INDEXATION_ECHEC", nom=nom_affiche)
    return agent_id


class ChoisirRolePayload(BaseModel):
    role: Literal["etablissement", "enseignant", "etudiant"]
    etablissement_id: Optional[str] = None
    enseignant_id: Optional[str] = None


class MonRoleReponse(BaseModel):
    role: Optional[str] = None
    etablissement_id: Optional[str] = None
    enseignant_id: Optional[str] = None
    agent_id: Optional[str] = None


@router.post("/choisir", response_model=MonRoleReponse)
def choisir_role(payload: ChoisirRolePayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    profil_existant = _lire_profil_role(utilisateur.id)
    if profil_existant and profil_existant.get("role"):
        raise erreur_api(409, "ROLE_DEJA_CHOISI")

    ligne_maj = {"role": payload.role}

    if payload.role == "enseignant":
        if not payload.etablissement_id:
            raise erreur_api(422, "ETABLISSEMENT_ID_REQUIS_POUR_ENSEIGNANT")
        cible = _lire_profil_role(payload.etablissement_id)
        if not cible or cible.get("role") != "etablissement":
            raise erreur_api(404, "ETABLISSEMENT_INTROUVABLE")
        ligne_maj["etablissement_id"] = payload.etablissement_id

    elif payload.role == "etudiant":
        if not payload.enseignant_id:
            raise erreur_api(422, "ENSEIGNANT_ID_REQUIS_POUR_ETUDIANT")
        cible = _lire_profil_role(payload.enseignant_id)
        if not cible or cible.get("role") != "enseignant":
            raise erreur_api(404, "ENSEIGNANT_INTROUVABLE")
        ligne_maj["enseignant_id"] = payload.enseignant_id

    # Upsert : même situation que PATCH /api/profiles/me, rien ne
    # garantit qu'une ligne `profiles` existe déjà (voir docstring
    # équivalente dans api/profiles.py).
    try:
        deja = supabase.table("profiles").select("slug").eq("user_id", utilisateur.id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification profil existant {utilisateur.id}) : {e}")
        deja = None

    nom_affiche = _nom_affiche_ou_repli(utilisateur.id) if deja and deja.data else "Sans nom"

    try:
        if deja and deja.data:
            supabase.table("profiles").update(ligne_maj).eq("user_id", utilisateur.id).execute()
        else:
            ligne_maj["user_id"] = utilisateur.id
            ligne_maj["slug"] = generer_id_depuis_nom(utilisateur.id[:8]) or utilisateur.id[:8]
            supabase.table("profiles").insert(ligne_maj).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (écriture rôle {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    agent_id = _creer_agent_minimal(utilisateur.id, payload.role, nom_affiche)

    journaliser(
        action="role.choisi",
        user_id=utilisateur.id,
        cible_type="profile",
        cible_id=utilisateur.id,
        details={"role": payload.role, "agent_id": agent_id},
        request=request,
    )

    return MonRoleReponse(
        role=payload.role,
        etablissement_id=ligne_maj.get("etablissement_id"),
        enseignant_id=ligne_maj.get("enseignant_id"),
        agent_id=agent_id,
    )


@router.get("/moi", response_model=MonRoleReponse)
def mon_role(utilisateur=Depends(utilisateur_courant)):
    profil = _lire_profil_role(utilisateur.id)
    if not profil or not profil.get("role"):
        return MonRoleReponse()
    try:
        agent = (
            supabase.table("agents")
            .select("id")
            .eq("owner_id", utilisateur.id)
            .order("created_at")
            .limit(1)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (agent du rôle {utilisateur.id}) : {e}")
        agent = None
    return MonRoleReponse(
        role=profil.get("role"),
        etablissement_id=profil.get("etablissement_id"),
        enseignant_id=profil.get("enseignant_id"),
        agent_id=(agent.data or {}).get("id") if agent and agent.data else None,
    )


class CompteListe(BaseModel):
    user_id: str
    nom_affiche: str


@router.get("/etablissements", response_model=List[CompteListe])
def lister_etablissements():
    """
    Public (pas d'auth) : alimente le menu déroulant "Ton établissement"
    du formulaire d'inscription enseignant.
    """
    try:
        res = supabase.table("profiles").select("user_id, nom_affiche").eq("role", "etablissement").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste établissements) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [
        CompteListe(user_id=r["user_id"], nom_affiche=r.get("nom_affiche") or "Sans nom") for r in (res.data or [])
    ]


@router.get("/enseignants", response_model=List[CompteListe])
def lister_enseignants(etablissement_id: Optional[str] = None):
    """
    Public (pas d'auth) : alimente le menu déroulant "Ton enseignant" du
    formulaire d'inscription étudiant. Filtrable par établissement si le
    front veut d'abord faire choisir l'établissement.
    """
    try:
        requete = supabase.table("profiles").select("user_id, nom_affiche").eq("role", "enseignant")
        if etablissement_id:
            requete = requete.eq("etablissement_id", etablissement_id)
        res = requete.execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste enseignants) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [
        CompteListe(user_id=r["user_id"], nom_affiche=r.get("nom_affiche") or "Sans nom") for r in (res.data or [])
    ]


class MembreEquipe(BaseModel):
    user_id: str
    nom_affiche: str
    agent_id: Optional[str] = None


@router.get("/mon-equipe", response_model=List[MembreEquipe])
def mon_equipe(utilisateur=Depends(utilisateur_courant)):
    """
    Enseignant connecté -> ses étudiants. Établissement connecté -> ses
    enseignants. Chaque membre inclut son `agent_id` (pour lier
    directement vers les pages "Modifier"/"Tester" déjà existantes côté
    frontend, aucune nouvelle page de gestion d'agent nécessaire).
    """
    profil = _lire_profil_role(utilisateur.id)
    if not profil or profil.get("role") not in ("enseignant", "etablissement"):
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    colonne_filtre = "enseignant_id" if profil["role"] == "enseignant" else "etablissement_id"
    try:
        membres = (
            supabase.table("profiles")
            .select("user_id, nom_affiche")
            .eq(colonne_filtre, utilisateur.id)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (mon-equipe {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    resultat = []
    for m in membres.data or []:
        try:
            agent = (
                supabase.table("agents")
                .select("id")
                .eq("owner_id", m["user_id"])
                .order("created_at")
                .limit(1)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (agent membre {m['user_id']}) : {e}")
            agent = None
        resultat.append(
            MembreEquipe(
                user_id=m["user_id"],
                nom_affiche=m.get("nom_affiche") or "Sans nom",
                agent_id=(agent.data or {}).get("id") if agent and agent.data else None,
            )
        )
    return resultat


class EnvoyerMessagePayload(BaseModel):
    destinataire_id: str
    contenu: str
    reponse_a: Optional[int] = None


class MessageDirect(BaseModel):
    id: int
    expediteur_id: str
    expediteur_nom: str
    destinataire_id: str
    contenu: str
    reponse_a: Optional[int] = None
    lu: bool
    created_at: str


def _peut_echanger_messages(moi: dict, cible: dict) -> bool:
    """Établissement <-> enseignant uniquement, et seulement s'ils sont rattachés l'un à l'autre."""
    if moi.get("role") == "etablissement" and cible.get("role") == "enseignant":
        return cible.get("etablissement_id") == moi.get("user_id")
    if moi.get("role") == "enseignant" and cible.get("role") == "etablissement":
        return moi.get("etablissement_id") == cible.get("user_id")
    return False


@router.post("/messages", response_model=MessageDirect, status_code=201)
def envoyer_message(payload: EnvoyerMessagePayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.contenu.strip():
        raise erreur_api(422, "MESSAGE_VIDE")

    moi = _lire_profil_role(utilisateur.id) or {}
    moi["user_id"] = utilisateur.id
    cible = _lire_profil_role(payload.destinataire_id)
    if not cible:
        raise erreur_api(404, "DESTINATAIRE_INTROUVABLE")
    cible["user_id"] = payload.destinataire_id

    if not _peut_echanger_messages(moi, cible):
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    try:
        res = (
            supabase.table("messages_directs")
            .insert(
                {
                    "expediteur_id": utilisateur.id,
                    "destinataire_id": payload.destinataire_id,
                    "contenu": payload.contenu.strip(),
                    "reponse_a": payload.reponse_a,
                }
            )
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (envoi message {utilisateur.id} -> {payload.destinataire_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    ligne = res.data[0]
    return MessageDirect(
        id=ligne["id"],
        expediteur_id=ligne["expediteur_id"],
        expediteur_nom=_nom_affiche_ou_repli(utilisateur.id),
        destinataire_id=ligne["destinataire_id"],
        contenu=ligne["contenu"],
        reponse_a=ligne.get("reponse_a"),
        lu=ligne["lu"],
        created_at=ligne["created_at"],
    )


@router.get("/messages", response_model=List[MessageDirect])
def lister_mes_messages(utilisateur=Depends(utilisateur_courant)):
    """Messages reçus ET envoyés (les deux sens), triés du plus récent au plus ancien."""
    try:
        res = (
            supabase.table("messages_directs")
            .select("*")
            .or_(f"destinataire_id.eq.{utilisateur.id},expediteur_id.eq.{utilisateur.id}")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (liste messages {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    return [
        MessageDirect(
            id=l["id"],
            expediteur_id=l["expediteur_id"],
            expediteur_nom=_nom_affiche_ou_repli(l["expediteur_id"]),
            destinataire_id=l["destinataire_id"],
            contenu=l["contenu"],
            reponse_a=l.get("reponse_a"),
            lu=l["lu"],
            created_at=l["created_at"],
        )
        for l in (res.data or [])
    ]


class AnnoncePayload(BaseModel):
    contenu: str


@router.post("/annonce", status_code=201)
def envoyer_annonce(payload: AnnoncePayload, request: Request, utilisateur=Depends(utilisateur_courant)):
    """
    Établissement -> tous ses enseignants + tous les étudiants de ces
    enseignants (confirmé par Bourama, 2026-08-04) -- PAS toute la
    plateforme. Le fan-out en notifications individuelles est fait par
    trigger Postgres (voir migration), pas ici.
    """
    if not payload.contenu.strip():
        raise erreur_api(422, "ANNONCE_VIDE")

    profil = _lire_profil_role(utilisateur.id)
    if not profil or profil.get("role") != "etablissement":
        raise erreur_api(403, "ACTION_RESERVEE_A_CE_ROLE")

    try:
        supabase.table("annonces_etablissement").insert(
            {"etablissement_id": utilisateur.id, "contenu": payload.contenu.strip()}
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (annonce établissement {utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="annonce.envoyee",
        user_id=utilisateur.id,
        cible_type="annonce_etablissement",
        cible_id=None,
        details={"longueur_contenu": len(payload.contenu.strip())},
        request=request,
    )
    return {"envoye": True}
