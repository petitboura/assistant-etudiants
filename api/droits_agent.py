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

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents/{agent_id}/droits", tags=["droits_agent"])


def _verifier_proprietaire(agent_id: str, user_id: str):
    try:
        res = supabase.table("agents").select("owner_id").eq("id", agent_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} pour droits) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de vérifier cet agent pour le moment.")
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != user_id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")


class OutilPlateforme(BaseModel):
    nom_outil: str
    categorie: int
    nom_serveur: str
    disponible: bool
    coche: bool  # deja active pour CET agent


class DroitsAgentReponse(BaseModel):
    generation: List[OutilPlateforme]  # categorie 1, par outil
    serveurs: List[OutilPlateforme]    # categories 2/3, par serveur (un seul outil "serveur_x" chacun)


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
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture droits agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les droits pour le moment.")

    noms_generation_coches = {l["nom_outil"] for l in (coches_generation_res.data or [])}
    noms_serveurs_coches = {l["nom_serveur"] for l in (coches_serveurs_res.data or [])}

    generation, serveurs = [], []
    for ligne in (registre_res.data or []):
        if ligne["categorie"] == 1:
            generation.append(OutilPlateforme(
                nom_outil=ligne["nom_outil"], categorie=1, nom_serveur=ligne["nom_serveur"],
                disponible=ligne["disponible"], coche=ligne["nom_outil"] in noms_generation_coches,
            ))
        else:
            serveurs.append(OutilPlateforme(
                nom_outil=ligne["nom_outil"], categorie=ligne["categorie"], nom_serveur=ligne["nom_serveur"],
                disponible=ligne["disponible"], coche=ligne["nom_serveur"] in noms_serveurs_coches,
            ))

    return DroitsAgentReponse(generation=generation, serveurs=serveurs)


class ModifierDroitsPayload(BaseModel):
    outils_generation: List[str] = []  # noms d'outils categorie 1 coches
    serveurs: List[str] = []           # noms de serveurs categories 2/3 coches
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
        avant = {l["nom_outil"] for l in (avant_gen_res.data or [])} | {l["nom_serveur"] for l in (avant_srv_res.data or [])}
        apres = set(payload.outils_generation) | set(payload.serveurs)

        supabase.table("agents_outils_generation").delete().eq("agent_id", agent_id).execute()
        supabase.table("agents_serveurs").delete().eq("agent_id", agent_id).execute()

        if payload.outils_generation:
            supabase.table("agents_outils_generation").insert(
                [{"agent_id": agent_id, "nom_outil": n} for n in payload.outils_generation]
            ).execute()
        if payload.serveurs:
            supabase.table("agents_serveurs").insert(
                [{"agent_id": agent_id, "nom_serveur": n} for n in payload.serveurs]
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification droits agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de modifier les droits pour le moment.")

    a_change = avant != apres
    if a_change and payload.informer_utilisateurs:
        ajouts = apres - avant
        retraits = avant - apres
        morceaux = []
        if ajouts:
            morceaux.append(f"Nouvelles capacités activées : {', '.join(sorted(ajouts))}")
        if retraits:
            morceaux.append(f"Capacités retirées : {', '.join(sorted(retraits))}")
        try:
            supabase.table("agent_updates").insert({
                "agent_id": agent_id,
                "user_id": utilisateur.id,
                "titre": "Mise à jour des capacités",
                "contenu": "\n".join(morceaux),
            }).execute()
        except Exception as e:
            # Ne bloque jamais la sauvegarde des droits pour un souci de
            # notification -- l'important est que les droits soient bien
            # enregistrés, l'info aux utilisateurs est secondaire.
            logging.error(f"ERREUR SUPABASE (agent_update auto droits agent={agent_id}) : {e}")

    journaliser(
        action="droits_agent.modifie",
        user_id=utilisateur.id,
        cible_type="agent",
        cible_id=agent_id,
        details={"outils_generation": payload.outils_generation, "serveurs": payload.serveurs},
    )

    return {"ok": True, "a_change": a_change}
