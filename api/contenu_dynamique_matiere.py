"""
Contenu dynamique par matière -- agent "Nitrux" (2026-08-06, demande
Bourama), pensé réutilisable pour d'autres agents du même genre. Voir
migrations/2026_08_06_contenu_dynamique_par_matiere.sql pour le schéma,
et core/contenu_dynamique_matiere.py pour la résolution du system_prompt
côté chat (routeur + fallback généraliste).

Indépendant de l'ancien système établissement/enseignant/étudiant
(désactivé) : ici, N'IMPORTE QUEL compte connecté peut écrire du contenu
pour une matière sur un agent marqué `contenu_dynamique_par_matiere`
(devient "enseignant" pour cette matière précise), et n'importe quel
compte peut entrer un code pour débloquer ce contenu ("étudiant"). Pas
de vérification de rôle ici, volontairement.
"""

import logging
import secrets
import string

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router_enseignant = APIRouter(prefix="/api/agents/{agent_id}/contenus-matiere", tags=["contenu_dynamique_matiere"])
router_etudiant = APIRouter(prefix="/api/agents/{agent_id}/rattachements", tags=["contenu_dynamique_matiere"])

# Alphabet sans caractères ambigus (0/O, 1/I/L) -- code pensé pour être
# recopié à la main par un étudiant depuis un tableau/une feuille.
_ALPHABET_CODE = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_LONGUEUR_CODE = 6
_TENTATIVES_MAX_CODE = 10


def _generer_code_unique() -> str:
    for _ in range(_TENTATIVES_MAX_CODE):
        code = "".join(secrets.choice(_ALPHABET_CODE) for _ in range(_LONGUEUR_CODE))
        try:
            existe = supabase.table("contenus_par_matiere").select("id").eq("code", code).maybe_single().execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification unicité code) : {e}")
            continue
        if not existe or not existe.data:
            return code
    raise erreur_api(500, "ERREUR_INCONNUE")


class ContenuMatierePayload(BaseModel):
    matiere: str
    system_prompt: str


class ContenuMatiere(BaseModel):
    id: str
    matiere: str
    system_prompt: str
    code: str


@router_enseignant.get("", response_model=list[ContenuMatiere])
def lister_mes_contenus(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """Les matières que CE compte a écrites pour cet agent (mes codes à partager)."""
    try:
        res = (
            supabase.table("contenus_par_matiere")
            .select("id, matiere, system_prompt, code")
            .eq("agent_id", agent_id)
            .eq("enseignant_id", utilisateur.id)
            .order("matiere")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture contenus_par_matiere {agent_id}/{utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    return [ContenuMatiere(**ligne) for ligne in (res.data or [])]


@router_enseignant.put("", response_model=ContenuMatiere)
def ecrire_contenu_matiere(agent_id: str, payload: ContenuMatierePayload, utilisateur=Depends(utilisateur_courant)):
    """
    Crée ou met à jour (même matière, même auteur = même ligne, voir
    contrainte UNIQUE(agent_id, enseignant_id, matiere)) le contenu d'une
    matière. Le code n'est généré qu'à la création -- une mise à jour du
    texte ne change jamais le code déjà partagé aux étudiants.
    """
    matiere = payload.matiere.strip()
    system_prompt = payload.system_prompt.strip()
    if not matiere or not system_prompt:
        raise erreur_api(400, "MATIERE_ET_SYSTEM_PROMPT_REQUIS")

    try:
        existant = (
            supabase.table("contenus_par_matiere")
            .select("id, code")
            .eq("agent_id", agent_id)
            .eq("enseignant_id", utilisateur.id)
            .eq("matiere", matiere)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification contenu existant {agent_id}/{utilisateur.id}/{matiere}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    if existant and existant.data:
        try:
            supabase.table("contenus_par_matiere").update(
                {"system_prompt": system_prompt, "updated_at": "now()"}
            ).eq("id", existant.data["id"]).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (mise à jour contenu {existant.data['id']}) : {e}")
            raise erreur_api(500, "ERREUR_INCONNUE")
        code = existant.data["code"]
        contenu_id = existant.data["id"]
    else:
        code = _generer_code_unique()
        try:
            res = (
                supabase.table("contenus_par_matiere")
                .insert(
                    {
                        "agent_id": agent_id,
                        "enseignant_id": utilisateur.id,
                        "matiere": matiere,
                        "system_prompt": system_prompt,
                        "code": code,
                    }
                )
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (création contenu {agent_id}/{utilisateur.id}/{matiere}) : {e}")
            raise erreur_api(500, "ERREUR_INCONNUE")
        contenu_id = res.data[0]["id"]

        journaliser(
            action="contenu_matiere.cree",
            user_id=utilisateur.id,
            cible_type="agent",
            cible_id=agent_id,
            details={"matiere": matiere},
        )

    return ContenuMatiere(id=contenu_id, matiere=matiere, system_prompt=system_prompt, code=code)


class RattachementPayload(BaseModel):
    code: str


class Rattachement(BaseModel):
    contenu_id: str
    matiere: str
    enseignant_nom: str
    actif: bool


def _nom_enseignant(enseignant_id: str) -> str:
    try:
        res = (
            supabase.table("profiles").select("nom_affiche").eq("user_id", enseignant_id).maybe_single().execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (nom enseignant {enseignant_id}) : {e}")
        return "Enseignant"
    return (res.data or {}).get("nom_affiche") or "Enseignant" if res else "Enseignant"


@router_etudiant.get("", response_model=list[Rattachement])
def lister_mes_rattachements(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """Toutes les matières débloquées par CE compte sur cet agent, actives ou non
    (les non-actives servent au bouton "changer d'enseignant" côté chat)."""
    try:
        res = (
            supabase.table("rattachements_par_matiere")
            .select("contenu_id, matiere, actif, contenus_par_matiere(enseignant_id)")
            .eq("agent_id", agent_id)
            .eq("etudiant_id", utilisateur.id)
            .order("matiere")
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rattachements {agent_id}/{utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    resultat = []
    for ligne in res.data or []:
        enseignant_id = (ligne.get("contenus_par_matiere") or {}).get("enseignant_id")
        resultat.append(
            Rattachement(
                contenu_id=ligne["contenu_id"],
                matiere=ligne["matiere"],
                enseignant_nom=_nom_enseignant(enseignant_id) if enseignant_id else "Enseignant",
                actif=ligne["actif"],
            )
        )
    return resultat


@router_etudiant.post("", response_model=Rattachement, status_code=201)
def entrer_code(agent_id: str, payload: RattachementPayload, utilisateur=Depends(utilisateur_courant)):
    code = payload.code.strip().upper()
    try:
        contenu = (
            supabase.table("contenus_par_matiere")
            .select("id, matiere, enseignant_id")
            .eq("agent_id", agent_id)
            .eq("code", code)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche code {code} pour agent {agent_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    if not contenu or not contenu.data:
        raise erreur_api(404, "CODE_INVALIDE")

    contenu_id = contenu.data["id"]
    matiere = contenu.data["matiere"]

    try:
        deja = (
            supabase.table("rattachements_par_matiere")
            .select("id")
            .eq("etudiant_id", utilisateur.id)
            .eq("contenu_id", contenu_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification rattachement existant) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if deja and deja.data:
        raise erreur_api(400, "DEJA_RATTACHE_A_CE_CONTENU")

    # Actif par défaut UNIQUEMENT si l'étudiant n'a encore aucun
    # rattachement actif pour cette matière (voir index unique partiel
    # côté base) -- sinon ce nouveau rattachement reste inactif, l'ancien
    # gardant la main tant que l'étudiant ne bascule pas explicitement.
    try:
        actif_existant = (
            supabase.table("rattachements_par_matiere")
            .select("id")
            .eq("etudiant_id", utilisateur.id)
            .eq("agent_id", agent_id)
            .eq("matiere", matiere)
            .eq("actif", True)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification actif existant {matiere}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    actif = not (actif_existant and actif_existant.data)

    try:
        supabase.table("rattachements_par_matiere").insert(
            {
                "agent_id": agent_id,
                "etudiant_id": utilisateur.id,
                "contenu_id": contenu_id,
                "matiere": matiere,
                "actif": actif,
            }
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (création rattachement {contenu_id}/{utilisateur.id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")

    journaliser(
        action="rattachement_matiere.cree",
        user_id=utilisateur.id,
        cible_type="agent",
        cible_id=agent_id,
        details={"matiere": matiere, "actif": actif},
    )

    return Rattachement(
        contenu_id=contenu_id,
        matiere=matiere,
        enseignant_nom=_nom_enseignant(contenu.data["enseignant_id"]),
        actif=actif,
    )


@router_etudiant.patch("/{contenu_id}/activer", status_code=204)
def activer_rattachement(agent_id: str, contenu_id: str, utilisateur=Depends(utilisateur_courant)):
    """Bouton "changer d'enseignant" dans le chat : bascule quel rattachement
    est actif pour la matière du contenu visé, tous les autres rattachements
    de cet étudiant pour la même matière repassent inactifs."""
    try:
        cible = (
            supabase.table("rattachements_par_matiere")
            .select("id, matiere")
            .eq("etudiant_id", utilisateur.id)
            .eq("agent_id", agent_id)
            .eq("contenu_id", contenu_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture rattachement à activer {contenu_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
    if not cible or not cible.data:
        raise erreur_api(404, "RATTACHEMENT_INTROUVABLE")

    matiere = cible.data["matiere"]
    try:
        # Désactive d'abord tout le monde sur cette matière (contrainte
        # unique partielle sinon violée par le passage à True ci-dessous),
        # puis active uniquement la cible.
        supabase.table("rattachements_par_matiere").update({"actif": False}).eq(
            "etudiant_id", utilisateur.id
        ).eq("agent_id", agent_id).eq("matiere", matiere).execute()
        supabase.table("rattachements_par_matiere").update({"actif": True}).eq("id", cible.data["id"]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (activation rattachement {contenu_id}) : {e}")
        raise erreur_api(500, "ERREUR_INCONNUE")
