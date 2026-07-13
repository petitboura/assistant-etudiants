"""
Étape C du plan (voir PIVOT_SOCIAL.md) : recherche pour la barre du feed
(`/`, voir tableau des pages). Recherche simple par nom (`ilike`), pas de
moteur dédié — la note de PIVOT_SOCIAL.md dit explicitement "pas besoin
de moteur de recherche dédié pour une v1".

Résultats créateurs identifiés par `user_id`, pas par `profiles.slug`
(voir docstring de `api/profiles.py` : génération de slug non tranchée).
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from api.auth import supabase

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/search", tags=["search"])


class ResultatAgent(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"


class ResultatCreateur(BaseModel):
    user_id: str
    nom_affiche: str = ""
    # Ajouté le 2026-07-13 (Bourama : les créateurs en résultat de
    # recherche s'affichaient en simple ligne de texte, contrairement à
    # l'onglet "Créateurs" du feed qui, lui, utilise déjà CreateurCard.tsx
    # -- même champs, même calcul de nombre_agents, pour que le frontend
    # puisse réutiliser exactement le même composant aux deux endroits.
    bio: str = ""
    avatar_url: Optional[str] = None
    nombre_agents: int = 0


class ResultatsRecherche(BaseModel):
    agents: List[ResultatAgent]
    createurs: List[ResultatCreateur]


@router.get("", response_model=ResultatsRecherche)
def rechercher(q: str = Query(..., min_length=1)):
    """
    Recherche par nom sur `agents.nom` et `profiles.nom_affiche`
    (`ilike`, insensible à la casse, correspondance partielle). Public,
    aucune auth. Limité à 20 résultats par catégorie, pas de pagination
    ni de scoring de pertinence pour cette v1.

    Les agents désactivés (`actif` est `False` explicitement) sont
    exclus, même convention "True par défaut" que `/api/feed`. Une des
    deux recherches peut échouer sans faire échouer l'autre
    (best-effort, comme le reste de l'API).
    """
    terme = f"%{q.strip()}%"

    try:
        agents_res = (
            supabase.table("agents")
            .select("id, nom, ui_config")
            .ilike("nom", terme)
            .or_("actif.is.null,actif.eq.true")
            .limit(20)
            .execute()
        )
        lignes_agents = agents_res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche agents, q={q}) : {e}")
        lignes_agents = []

    try:
        createurs_res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, bio, avatar_url")
            .ilike("nom_affiche", terme)
            .limit(20)
            .execute()
        )
        lignes_createurs = createurs_res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (recherche créateurs, q={q}) : {e}")
        lignes_createurs = []

    # Même pattern de comptage groupé que GET /api/creators
    # (lister_createurs) : une requête, pas une par créateur.
    comptes_par_user_id: dict = {}
    ids_uniques = [ligne["user_id"] for ligne in lignes_createurs]
    if ids_uniques:
        try:
            agents_res = (
                supabase.table("agents")
                .select("owner_id")
                .in_("owner_id", ids_uniques)
                .execute()
            )
            for a in agents_res.data or []:
                oid = a.get("owner_id")
                if oid:
                    comptes_par_user_id[oid] = comptes_par_user_id.get(oid, 0) + 1
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (comptage agents, recherche créateurs, q={q}) : {e}")

    return ResultatsRecherche(
        agents=[
            ResultatAgent(
                id=ligne["id"],
                nom=ligne["nom"],
                icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
            )
            for ligne in lignes_agents
        ],
        createurs=[
            ResultatCreateur(
                user_id=ligne["user_id"],
                nom_affiche=ligne.get("nom_affiche") or "",
                bio=ligne.get("bio") or "",
                avatar_url=ligne.get("avatar_url"),
                nombre_agents=comptes_par_user_id.get(ligne["user_id"], 0),
            )
            for ligne in lignes_createurs
        ],
    )
