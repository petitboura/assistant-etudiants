"""
Étape C du plan (voir PIVOT_SOCIAL.md) : portfolio public d'un créateur
et édition de son propre profil (table `profiles`).

Décision prise faute de réponse tranchée sur la génération de
`profiles.slug` (voir PIVOT_SOCIAL.md, Étape B, "pas décidé encore") :
ces routes utilisent `user_id` directement comme clé d'URL, PAS un
slug. Même repli que celui déjà fait pour `GET /api/agents/{agent_id}`
(qui utilise `agents.id`, pas une colonne slug dédiée non plus). À
remplacer par `profiles.slug` quand sa génération sera décidée et
implémentée — traiter ça comme un changement d'API (l'URL change de
forme), pas une simple substitution de colonne dans la requête.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, supabase
from creation_agent import generer_id_depuis_nom

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfilPublic(BaseModel):
    user_id: str
    nom_affiche: str = ""
    bio: str = ""
    avatar_url: Optional[str] = None


class AgentDuCreateur(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"
    image_vitrine_url: Optional[str] = None
    description: str = ""


class ProfilDetailPublic(ProfilPublic):
    agents: List[AgentDuCreateur] = Field(default_factory=list)


@router.get("/{user_id}", response_model=ProfilDetailPublic)
def obtenir_profil_public(user_id: str):
    """
    Portfolio public d'un créateur, pour `/u/[slug]` (en pratique
    `/u/{user_id}` pour l'instant, voir docstring du module). Public,
    aucune auth. Inclut la liste de ses agents actifs (même convention
    "True par défaut" que `/api/feed`), pour le tableau des pages du
    frontend ("Liste des agents du créateur, bouton Follow").

    404 si aucun profil n'existe pour ce `user_id` : un compte tout juste
    inscrit sans `PATCH /me` préalable n'a pas encore de portfolio public
    (voir docstring de `mettre_a_jour_mon_profil`, pas de trigger de
    création automatique décidé).

    L'échec de la lecture des agents n'annule pas la réponse (best-effort,
    même logique que l'indexation de texte libre à la création d'agent) :
    mieux vaut un profil sans liste d'agents qu'une 500 sur tout.
    """
    try:
        profil = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, bio, avatar_url")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture profil {user_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger ce profil pour le moment.")

    if not profil or not profil.data:
        raise HTTPException(status_code=404, detail="Profil introuvable.")

    try:
        agents_res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description")
            .eq("owner_id", user_id)
            .or_("actif.is.null,actif.eq.true")
            .execute()
        )
        lignes_agents = agents_res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents du créateur {user_id}) : {e}")
        lignes_agents = []

    agents = [
        AgentDuCreateur(
            id=ligne["id"],
            nom=ligne["nom"],
            icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
            image_vitrine_url=ligne.get("image_vitrine_url"),
            description=ligne.get("description") or "",
        )
        for ligne in lignes_agents
    ]

    ligne = profil.data
    return ProfilDetailPublic(
        user_id=ligne["user_id"],
        nom_affiche=ligne.get("nom_affiche") or "",
        bio=ligne.get("bio") or "",
        avatar_url=ligne.get("avatar_url"),
        agents=agents,
    )


class MettreAJourProfilPayload(BaseModel):
    nom_affiche: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None


@router.patch("/me", response_model=ProfilPublic)
def mettre_a_jour_mon_profil(
    payload: MettreAJourProfilPayload, utilisateur=Depends(utilisateur_courant)
):
    """
    Crée ou met à jour le profil de l'utilisateur courant. Upsert, pas un
    simple update : rien ne garantit qu'une ligne `profiles` existe déjà
    pour ce `user_id` (pas de trigger de création automatique à
    l'inscription — voir PIVOT_SOCIAL.md, Étape B, "profiles.slug...
    rien ne la remplit encore"). Le premier appel de ce endpoint sert
    donc aussi de création de profil.

    PATCH partiel : un champ omis (None) n'est pas modifié.

    Bug corrigé le 2026-07-12 (500 remonté par Bourama, capture d'écran
    /dashboard) : `profiles.slug` est NOT NULL + UNIQUE en base, mais
    rien ne le remplissait jamais ici — le tout premier upsert pour
    n'importe quel compte échouait systématiquement (violation de
    contrainte NOT NULL, avalée par le except générique et renvoyée comme
    500 opaque). Généré une seule fois, à la création de la ligne
    (jamais régénéré sur les PATCH suivants — une fois que
    `profiles.slug` remplacera `user_id` dans les URLs `/u/...`, un lien
    déjà partagé ne doit pas changer sous les pieds de la personne).

    Bug bis corrigé le 2026-07-12 (même jour, la 500 persistait malgré le
    fix ci-dessus) : la détection "ce profil existe déjà" utilisait la
    vérité Python de `deja_existant.data` directement, qui peut être un
    objet non-None mais "vide" (valeurs null dedans) selon le client
    Supabase utilisé pour une recherche sans résultat — donc considéré
    VRAI par Python alors qu'aucune ligne n'existe réellement, ce qui
    sautait la génération du slug pour un compte qui en avait pourtant
    besoin. Voir `profil_existe_deja` ci-dessous : vérifie maintenant la
    présence explicite d'un `user_id` dans les données plutôt que la
    simple vérité de l'objet.
    """
    ligne = {"user_id": utilisateur.id}
    if payload.nom_affiche is not None:
        ligne["nom_affiche"] = payload.nom_affiche.strip()
    if payload.bio is not None:
        ligne["bio"] = payload.bio.strip()
    if payload.avatar_url is not None:
        ligne["avatar_url"] = payload.avatar_url

    try:
        deja_existant = (
            supabase.table("profiles")
            .select("user_id")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification profil existant {utilisateur.id}) : {e}")
        deja_existant = None

    # Bug trouvé le 2026-07-12 (Bourama a remonté le message d'erreur
    # détaillé, capture d'écran) : `deja_existant.data` peut être un objet
    # "vide" mais non-None (ex. dict avec des valeurs null dedans) selon
    # le comportement de .maybe_single() côté client Supabase — ce qui le
    # rend VRAI au sens Python (`{...} and ...` est truthy dès que le dict
    # n'est pas vide, même s'il ne contient que des valeurs null), et donc
    # sautait la génération du slug en pensant qu'une ligne existait déjà.
    # Résultat : Postgres tentait quand même une VRAIE création (aucune
    # ligne ne correspondait à l'upsert), sans slug -> violation NOT NULL.
    # Fix : vérifier explicitement la présence d'un user_id dans data, pas
    # juste la "vérité" Python de l'objet.
    profil_existe_deja = bool(
        deja_existant and deja_existant.data and deja_existant.data.get("user_id")
    )

    if not profil_existe_deja:
        base = generer_id_depuis_nom(payload.nom_affiche or "") or utilisateur.id[:8]
        slug = base
        try:
            collision = (
                supabase.table("profiles").select("user_id").eq("slug", slug).maybe_single().execute()
            )
            if collision and collision.data:
                slug = f"{base}-{utilisateur.id[:6]}"
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification unicité slug={slug}) : {e}")
            slug = f"{base}-{utilisateur.id[:6]}"
        ligne["slug"] = slug

    try:
        supabase.table("profiles").upsert(ligne, on_conflict="user_id").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (upsert profil {utilisateur.id}) : {e}")
        # DEBUG TEMPORAIRE (2026-07-12, 3e tentative) : les deux bugs
        # précédents (slug NOT NULL, puis vérité Python trompeuse sur
        # deja_existant.data) sont corrigés mais la 500 persiste encore
        # -- donc une 3e cause distincte. Expose le message réel le temps
        # de la trouver ; À RETIRER une fois corrigé, ne pas garder ça en
        # prod (voir même remarque déjà faite lors du 2e bug).
        raise HTTPException(status_code=500, detail=f"Impossible de mettre à jour le profil : {e}")

    try:
        res = (
            supabase.table("profiles")
            .select("user_id, nom_affiche, bio, avatar_url")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (relecture profil {utilisateur.id}) : {e}")
        raise HTTPException(
            status_code=500, detail="Profil mis à jour mais impossible de le relire pour confirmation."
        )

    resultat = res.data or ligne
    return ProfilPublic(
        user_id=resultat["user_id"],
        nom_affiche=resultat.get("nom_affiche") or "",
        bio=resultat.get("bio") or "",
        avatar_url=resultat.get("avatar_url"),
    )
