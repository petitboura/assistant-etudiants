"""
Backend API — remplace progressivement les interfaces Streamlit.
Voir api/PLAN.md pour la séquence complète et l'état d'avancement.

Lancement local : uvicorn api.main:app --reload --port 8000
"""

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api.auth import utilisateur_courant, supabase
from api.agents import router as agents_router
from api.creators import router as creators_router
from api.profiles import router as profiles_router
from api.search import router as search_router
from api.uploads import router as uploads_router
from api.historique import router as historique_router
from api.notifications import router as notifications_router
from api.agent_updates import router as agent_updates_router
from api.posts import router as posts_router
from api.chat import router as chat_router
from api.feedback import router as feedback_router
from api.generation import router as generation_router
from api.memoire import router as memoire_router
from core.serveur_mcp_generation import mcp_generation
from core.serveur_mcp_github import mcp_github

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Requis par FastMCP (stateless_http=True) : le session_manager du
    # serveur MCP de génération (voir core/serveur_mcp_generation.py) a
    # besoin de tourner pendant toute la durée de vie du process, sinon
    # streamable_http_app() renvoie une erreur "Task group is not
    # initialized" au premier appel d'outil.
    async with mcp_generation.session_manager.run(), mcp_github.session_manager.run():
        yield


app = FastAPI(title="Djiguigne API", version="0.1.0", lifespan=_lifespan)

# Serveur MCP interne (documents/code/images), monté en sous-application
# ASGI : voir core/serveur_mcp_generation.py pour le detail des outils, et
# registre_outils.py pour son enregistrement côté agent (nom "generation").
# streamable_http_path="/" (configuré dans le serveur lui-même) fait que
# le point d'entree final est bien /mcp/generation, sans /mcp en trop.
app.mount("/mcp/generation", mcp_generation.streamable_http_app())

# Domaines autorisés à appeler cette API. "http://localhost:3000" est le
# port par defaut de `npm run dev` en Next.js, a garder tant que le
# frontend n'est pas deploye. A completer avec le vrai domaine une fois
# app.djiguigne.com cree (Etape 5 du PLAN.md).
# Domaines fixes autorisés (pas de motif possible pour ceux-la).
ORIGINES_AUTORISEES = [
    "http://localhost:3000",
    "https://app.djiguigne.com",
    "https://djiguign-ai.vercel.app",
]

# En plus des domaines fixes ci-dessus : Vercel donne une URL DIFFERENTE
# a chaque deploiement (en plus de l'alias stable djiguign-ai.vercel.app),
# donc une liste figee doit etre corrigee a la main a chaque fois. Ce
# motif autorise automatiquement toutes les URLs Vercel de CE projet
# (elles commencent toutes par "djiguign", ex. djiguign-ai.vercel.app,
# djiguign-pgwfo47je-petitbouras-projects.vercel.app), sans avoir a
# retoucher ce fichier a chaque nouveau lien.
MOTIF_ORIGINES_VERCEL = r"https://djiguign[a-z0-9\-]*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_AUTORISEES,
    allow_origin_regex=MOTIF_ORIGINES_VERCEL,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents_router)
app.include_router(creators_router)
app.include_router(profiles_router)
app.include_router(search_router)
app.include_router(uploads_router)
app.include_router(historique_router)
app.include_router(notifications_router)
app.include_router(agent_updates_router)
app.include_router(posts_router)
app.include_router(chat_router)
app.include_router(feedback_router)
app.include_router(generation_router)
app.include_router(memoire_router)


@app.get("/health")
def health():
    """Verification basique : l'API repond, sans dependance a Supabase."""
    return {"status": "ok"}


@app.get("/health/me")
def health_me(utilisateur=Depends(utilisateur_courant)):
    """
    Verification de bout en bout de l'auth : necessite un vrai token
    Supabase valide en en-tete Authorization. Sert a valider, avant de
    construire quoi que ce soit d'autre, que le frontend arrive bien a
    s'authentifier aupres de cette API. A garder meme apres l'Etape 0
    (utile pour deboguer un token en prod).
    """
    return {"id": utilisateur.id, "email": utilisateur.email}


class AgentFeedItem(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"
    image_vitrine_url: Optional[str] = None
    description: str = ""
    categorie_id: Optional[str] = None


class FeedReponse(BaseModel):
    agents: List[AgentFeedItem]
    page: int
    limite: int
    total: int


@app.get("/api/feed", response_model=FeedReponse)
def feed(
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
    categorie: Optional[str] = Query(None),
):
    """
    Liste paginée des agents publiés, pour le feed de découverte de la
    page `/` (voir PIVOT_SOCIAL.md). Public, aucune auth requise.

    Un agent est considéré publié si `actif` est True OU absent/NULL
    (même convention de "True par défaut" que
    faces/vues/chat.py:_agent_est_actif, pour ne pas faire disparaître du
    feed des agents créés avant l'ajout de cette colonne).

    `categorie` (ajouté 2026-07-15, système de catégories) : filtre par
    `categorie_id` si fourni, sinon comportement inchangé (tout le feed).
    """
    debut = (page - 1) * limite
    fin = debut + limite - 1

    try:
        requete = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description, categorie_id", count="exact")
            .or_("actif.is.null,actif.eq.true")
        )
        if categorie:
            requete = requete.eq("categorie_id", categorie)
        res = requete.order("id").range(debut, fin).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture feed, page={page}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger le feed pour le moment.")

    agents = [
        AgentFeedItem(
            id=ligne["id"],
            nom=ligne["nom"],
            icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
            image_vitrine_url=ligne.get("image_vitrine_url"),
            description=ligne.get("description") or "",
            categorie_id=ligne.get("categorie_id"),
        )
        for ligne in (res.data or [])
    ]

    return FeedReponse(agents=agents, page=page, limite=limite, total=res.count or 0)


class CategorieItem(BaseModel):
    id: str
    nom: str
    mots_cles: List[str] = []
    parent_id: Optional[str] = None


@app.get("/api/categories", response_model=List[CategorieItem])
def lister_categories(seulement_utilisees: bool = Query(False)):
    """
    Toutes les catégories, pour le popup de sélection sur la page
    d'accueil et les formulaires de création/modification d'agent.
    Public, aucune auth requise (même statut que /api/feed). `parent_id`
    prépare l'arrivée des sous-catégories (Bourama, 2026-07-15) : NULL
    pour toutes pour l'instant, aucune catégorie n'est encore un enfant
    d'une autre.

    `seulement_utilisees` (ajouté 2026-07-15, demande de Bourama : les
    catégories vides ne doivent pas apparaître à l'accueil) : si True, ne
    renvoie que les catégories ayant au moins un agent publié. UNIQUEMENT
    pour le popup de l'accueil -- les formulaires de création/modification
    continuent d'appeler cette route SANS ce paramètre, pour permettre de
    choisir une catégorie même si on est le premier agent dedans.
    """
    try:
        res = supabase.table("categories").select("id, nom, mots_cles, parent_id").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture categories) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les catégories pour le moment.")

    categories = res.data or []

    if seulement_utilisees:
        try:
            res_agents = (
                supabase.table("agents")
                .select("categorie_id")
                .or_("actif.is.null,actif.eq.true")
                .not_.is_("categorie_id", "null")
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture categorie_id des agents) : {e}")
            raise HTTPException(status_code=500, detail="Impossible de charger les catégories pour le moment.")
        ids_utilisees = {l["categorie_id"] for l in (res_agents.data or [])}
        categories = [c for c in categories if c["id"] in ids_utilisees]

    return [CategorieItem(**ligne) for ligne in categories]
