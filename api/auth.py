"""
Verifie le token Supabase envoye par le frontend Next.js (en-tete
`Authorization: Bearer <access_token>`), sans jamais gerer de mot de
passe cote API : l'inscription/connexion se font entierement dans
Next.js via le SDK JS Supabase (voir la decision d'architecture #1 dans
api/PLAN.md).
"""

import os
import logging
from fastapi import Header, HTTPException
from supabase import create_client

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    """
    Contrairement a core/*.py (qui tourne sous Streamlit et lit
    st.secrets), cette API tourne sous Railway/uvicorn : uniquement des
    variables d'environnement, jamais st.secrets.
    """
    return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : l'auth API sera toujours en echec.")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def utilisateur_courant(authorization: str = Header(default=None)):
    """
    Dependance FastAPI a utiliser sur toute route protegee :

        @app.post("/api/agents")
        def creer_agent(payload: ..., utilisateur=Depends(utilisateur_courant)):
            owner_id = utilisateur.id
            ...

    Leve une 401 si le token est absent, mal forme, ou invalide/expire.
    Ne verifie PAS de permissions metier (ex: "est-ce le proprietaire de
    cet agent ?") : ca reste a la charge de chaque route (voir Etape 2 du
    PLAN.md, verification owner_id).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token d'authentification manquant")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Token d'authentification manquant")

    try:
        reponse = supabase.auth.get_user(token)
    except Exception as e:
        logging.error(f"ERREUR verification token Supabase : {e}")
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")

    if not reponse or not reponse.user:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")

    return reponse.user
