"""
Backend API — remplace progressivement les interfaces Streamlit.
Voir api/PLAN.md pour la séquence complète et l'état d'avancement.

Lancement local : uvicorn api.main:app --reload --port 8000
"""

import logging
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

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Djiguigne API", version="0.1.0")

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
def lister_categories():
    """
    Toutes les catégories, pour le popup de sélection sur la page
    d'accueil et les formulaires de création/modification d'agent.
    Public, aucune auth requise (même statut que /api/feed). `parent_id`
    prépare l'arrivée des sous-catégories (Bourama, 2026-07-15) : NULL
    pour toutes pour l'instant, aucune catégorie n'est encore un enfant
    d'une autre.
    """
    try:
        res = supabase.table("categories").select("id, nom, mots_cles, parent_id").execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture categories) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les catégories pour le moment.")

    return [CategorieItem(**ligne) for ligne in (res.data or [])]
