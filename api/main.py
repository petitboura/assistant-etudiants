"""
Backend API — remplace progressivement les interfaces Streamlit.
Voir api/PLAN.md pour la séquence complète et l'état d'avancement.

Lancement local : uvicorn api.main:app --reload --port 8000
"""

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from api.auth import utilisateur_courant
from api.agents import router as agents_router

app = FastAPI(title="Djiguigne API", version="0.1.0")

# Domaines autorisés à appeler cette API. "http://localhost:3000" est le
# port par defaut de `npm run dev` en Next.js, a garder tant que le
# frontend n'est pas deploye. A completer avec le vrai domaine une fois
# app.djiguigne.com cree (Etape 5 du PLAN.md).
ORIGINES_AUTORISEES = [
    "http://localhost:3000",
    "https://app.djiguigne.com",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_AUTORISEES,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents_router)


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
