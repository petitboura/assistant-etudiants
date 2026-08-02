"""
Droits par agent -- categories 1 (generation, par outil), 2 (serveur
externe global, par serveur) et 3 (compte utilisateur, par serveur).
Categories 4/5 (connexions OAuth createur/plateforme) pas couvertes ici,
voir connexions/notion.py pour le pattern OAuth existant a etendre.

Principe allow-list : le formulaire lit TOUJOURS registre_outils_plateforme
en direct (jamais une liste figee cote frontend) et calcule les cases a
cocher a partir de ca, croisees avec ce que l'agent a deja coche. Un
outil retire du registre disparait automatiquement du formulaire, sans
rien a changer ici.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List

from api.auth import utilisateur_courant, supabase
from api.journal import journaliser
from core.erreurs import erreur_api

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents/{agent_id}/droits", tags=["droits_agent"])

# Registre brut, sans agent -- utilisé par le formulaire de CRÉATION
# (l'agent n'existe pas encore, donc pas de "coche" possible, juste la
# liste de ce qui est proposable). Route à part, hors du prefix
# {agent_id} ci-dessus.
router_registre = APIRouter(prefix="/api/registre-outils", tags=["droits_agent"])


@router_registre.get("")
def lire_registre_outils(utilisateur=Depends(utilisateur_courant)):
    try:
        res = supabase.table("registre_outils_plateforme").select("*").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture registre_outils_plateforme) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_CHARGER_LE_REGISTRE_POUR")
    generation = [l for l in (res.data or []) if l["categorie"] == 1]
    serveurs = [l for l in (res.data or []) if l["categorie"] in (2, 3)]
    actions_locales = [l for l in (res.data or []) if l["categorie"] == 4]
    return {"generation": generation, "serveurs": serveurs, "actions_locales": actions_locales}


def _verifier_proprietaire(agent_id: str, user_id: str):
    try:
        res = supabase.table("agents").select("owner_id").eq("id", agent_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} pour droits) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_VERIFIER_CET_AGENT_POUR")
    if not res or not res.data:
        raise erreur_api(404, "AGENT_INTROUVABLE")
    if res.data["owner_id"] != user_id:
        raise erreur_api(403, "CET_AGENT_NE_T_APPARTIENT_PAS")


class OutilPlateforme(BaseModel):
    nom_outil: str
    categorie: int
    nom_serveur: str
    disponible: bool
    coche: bool  # deja active pour CET agent


class DroitsAgentReponse(BaseModel):
    generation: List[OutilPlateforme]       # categorie 1, par outil
    serveurs: List[OutilPlateforme]         # categories 2/3, par serveur (un seul outil "serveur_x" chacun)
    actions_locales: List[OutilPlateforme]  # categorie 4, par action (pas envoyees au LLM, juste UI chat)


@router.get("", response_model=DroitsAgentReponse)
def lire_droits_agent(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    _verifier_proprietaire(agent_id, utilisateur.id)

    try:
        registre_res = supabase.table("registre_outils_plateforme").select("*").execute()
        coches_generation_res = (
            supabase.table("agents_outils_generation").select("nom_outil").eq("agent_id", agent_id).execute()
        )
        coches_serveurs_res = (
            supabase.table("agents_serveurs").select("nom_serveur").eq("agent_id", agent_id).execute()
        )
        coches_locales_res = (
            supabase.table("agents_actions_locales").select("nom_action").eq("agent_id", agent_id).execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture droits agent={agent_id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_CHARGER_LES_DROITS_POUR")

    noms_generation_coches = {l["nom_outil"] for l in (coches_generation_res.data or [])}
    noms_serveurs_coches = {l["nom_serveur"] for l in (coches_serveurs_res.data or [])}
    noms_locales_coches = {l["nom_action"] for l in (coches_locales_res.data or [])}

    generation, serveurs, actions_locales = [], [], []
    for ligne in (registre_res.data or []):
        if ligne["categorie"] == 1:
            generation.append(OutilPlateforme(
                nom_outil=ligne["nom_outil"], categorie=1, nom_serveur=ligne["nom_serveur"],
                disponible=ligne["disponible"], coche=ligne["nom_outil"] in noms_generation_coches,
            ))
        elif ligne["categorie"] == 4:
            actions_locales.append(OutilPlateforme(
                nom_outil=ligne["nom_outil"], categorie=4, nom_serveur=ligne["nom_serveur"],
                disponible=ligne["disponible"], coche=ligne["nom_outil"] in noms_locales_coches,
            ))
        else:
            serveurs.append(OutilPlateforme(
                nom_outil=ligne["nom_outil"], categorie=ligne["categorie"], nom_serveur=ligne["nom_serveur"],
                disponible=ligne["disponible"], coche=ligne["nom_serveur"] in noms_serveurs_coches,
            ))

    return DroitsAgentReponse(generation=generation, serveurs=serveurs, actions_locales=actions_locales)


class ModifierDroitsPayload(BaseModel):
    outils_generation: List[str] = []   # noms d'outils categorie 1 coches
    serveurs: List[str] = []            # noms de serveurs categories 2/3 coches
    actions_locales: List[str] = []     # noms d'actions categorie 4 coches (pas envoyees au LLM)
    informer_utilisateurs: bool = True  # case cochee par defaut (agent_updates)


@router.patch("")
def modifier_droits_agent(agent_id: str, payload: ModifierDroitsPayload, utilisateur=Depends(utilisateur_courant)):
    _verifier_proprietaire(agent_id, utilisateur.id)

    try:
        avant_gen_res = (
            supabase.table("agents_outils_generation").select("nom_outil").eq("agent_id", agent_id).execute()
        )
        avant_srv_res = (
            supabase.table("agents_serveurs").select("nom_serveur").eq("agent_id", agent_id).execute()
        )
        avant_loc_res = (
            supabase.table("agents_actions_locales").select("nom_action").eq("agent_id", agent_id).execute()
        )
        avant = (
            {l["nom_outil"] for l in (avant_gen_res.data or [])}
            | {l["nom_serveur"] for l in (avant_srv_res.data or [])}
            | {l["nom_action"] for l in (avant_loc_res.data or [])}
        )
        apres = set(payload.outils_generation) | set(payload.serveurs) | set(payload.actions_locales)

        supabase.table("agents_outils_generation").delete().eq("agent_id", agent_id).execute()
        supabase.table("agents_serveurs").delete().eq("agent_id", agent_id).execute()
        supabase.table("agents_actions_locales").delete().eq("agent_id", agent_id).execute()

        if payload.outils_generation:
            supabase.table("agents_outils_generation").insert(
                [{"agent_id": agent_id, "nom_outil": n} for n in payload.outils_generation]
            ).execute()
        if payload.serveurs:
            supabase.table("agents_serveurs").insert(
                [{"agent_id": agent_id, "nom_serveur": n} for n in payload.serveurs]
            ).execute()
        if payload.actions_locales:
            supabase.table("agents_actions_locales").insert(
                [{"agent_id": agent_id, "nom_action": n} for n in payload.actions_locales]
            ).execute()

        # CORRECTION : agents.tools_enabled est une ancienne colonne qui
        # n'est plus lue par le moteur MCP (voir mcp_tools.py), mais qui
        # restait figee a sa valeur de creation des la premiere
        # modification des droits, ce qui pretait a confusion. On la
        # resynchronise ici (categories grossieres, a titre d'affichage
        # seulement) pour qu'elle ne mente plus.
        tools_enabled_legacy = sorted(
            ({"generation"} if payload.outils_generation else set())
            | set(payload.serveurs)
            | ({"ui"} if payload.actions_locales else set())
        )
        supabase.table("agents").update({"tools_enabled": tools_enabled_legacy}).eq("id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification droits agent={agent_id}) : {e}")
        raise erreur_api(500, "IMPOSSIBLE_DE_MODIFIER_LES_DROITS_POUR")

    # CORRECTION (Bourama, 02/08) : on n'insère plus automatiquement de
    # ligne dans agent_updates ici. La section "Mises à jour" de la page
    # publique de l'agent doit uniquement contenir ce que le créateur
    # écrit lui-même via "Modifier agent" (voir api/agent_updates.py,
    # publier_mise_a_jour) -- pas de texte généré à partir des noms
    # d'outils/serveurs activés. On garde a_change pour la réponse de
    # l'endpoint (utilisé par le frontend).
    a_change = avant != apres

    journaliser(
        action="droits_agent.modifie",
        user_id=utilisateur.id,
        cible_type="agent",
        cible_id=agent_id,
        details={
            "outils_generation": payload.outils_generation,
            "serveurs": payload.serveurs,
            "actions_locales": payload.actions_locales,
        },
    )

    return {"ok": True, "a_change": a_change}
