"""
Expose connexions/oauth_generique.py (moteur OAuth générique, un service =
une entrée dans SERVICES) au frontend Next.js. Un seul routeur pour TOUS
les services qui suivent ce moteur générique (GitHub pour l'instant,
d'autres plus tard sans nouveau code ici) -- pas un routeur par service.

Flux, vu du frontend :
1. GET /api/connexions/{service}/statut -> savoir si afficher "Connecter"
   ou "Connecté".
2. GET /api/connexions/{service}/demarrer -> ouvre l'URL renvoyée (le
   navigateur navigue chez le fournisseur, ex. github.com/login/oauth).
3. Le fournisseur redirige vers URL_RETOUR_APP (une page dédiée côté
   Next.js, ex. /oauth/retour) avec ?code=...&state=... dans l'URL.
4. Cette page appelle POST /api/connexions/finaliser {code, state} --
   PAS besoin de préciser `service` ici, il est retrouvé depuis `state`
   (voir etat_en_attente, pensé exactement pour ce cas : une URL de
   callback partagée entre plusieurs services).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from connexions.oauth_generique import demarrer_connexion, est_connecte, etat_en_attente, finaliser_connexion

router = APIRouter(prefix="/api/connexions", tags=["connexions"])


@router.get("/{service}/statut")
def statut_connexion(service: str, utilisateur=Depends(utilisateur_courant)):
    return {"connecte": est_connecte(service, utilisateur.id)}


@router.get("/diagnostic/{service}")
def diagnostic_config(service: str):
    """
    Diagnostic SANS secret exposé -- juste des booléens (présent/absent)
    pour savoir si le process qui tourne réellement voit la config
    attendue. Ajouté le 2026-07-23 après un premier vrai test en
    production : le message "configuration manquante côté serveur" ne
    dit pas LAQUELLE des deux valeurs manque, ni si Railway a bien
    redéployé après l'ajout des variables. `URL_RETOUR_APP` n'est pas un
    secret (juste une URL publique de callback) -- renvoyée en clair,
    utile pour repérer une faute de frappe/un mauvais domaine.
    """
    from connexions.oauth_generique import SERVICES, URL_RETOUR, get_secret

    config = SERVICES.get(service)
    if not config:
        return {"erreur": f"Service '{service}' inconnu dans SERVICES."}

    return {
        "client_id_present": bool(get_secret(config["client_id_env"])),
        "client_secret_present": bool(get_secret(config.get("client_secret_env", ""))),
        "url_retour_app": URL_RETOUR,
    }


@router.get("/{service}/demarrer")
def demarrer(service: str, agent_id: str = "", utilisateur=Depends(utilisateur_courant)):
    url = demarrer_connexion(service, utilisateur.id, agent_id or None)
    if not url:
        return {"url": None, "erreur": f"Connexion {service} indisponible (configuration manquante côté serveur)."}
    return {"url": url}


class FinaliserPayload(BaseModel):
    code: str
    state: str


@router.post("/finaliser")
def finaliser(payload: FinaliserPayload):
    # Pas d'auth requise ici : cette route est appelée par la page de
    # callback juste après la redirection du fournisseur OAuth, avant
    # tout retour à une session applicative classique -- le `state`
    # (opaque, généré côté serveur, à usage unique) fait déjà office de
    # preuve d'origine, voir demarrer_connexion/finaliser_connexion.
    service = etat_en_attente(payload.state)
    if not service:
        return {"succes": False, "message": "Session de connexion expirée ou déjà utilisée.", "service": None}
    succes, message = finaliser_connexion(service, payload.code, payload.state)
    return {"succes": succes, "message": message, "service": service}


@router.get("/github/depots")
def depots_github(utilisateur=Depends(utilisateur_courant)):
    """
    Liste les dépôts (publics ET privés) de la personne connectée --
    voir BarreDeSaisie.tsx, sélecteur ouvert au clic sur le bouton GitHub
    une fois connecté. Nécessite obligatoirement le token OAuth de la
    personne (pas de repli sur un token de plateforme) : sans ça, on ne
    verrait que des dépôts publics au hasard, pas "ses" dépôts.
    """
    import requests
    from connexions.oauth_generique import obtenir_token_valide

    token = obtenir_token_valide("github", utilisateur.id)
    if not token:
        return {"depots": [], "erreur": "Compte GitHub non connecté."}

    try:
        reponse = requests.get(
            "https://api.github.com/user/repos",
            timeout=10,
            headers={"Authorization": f"Bearer {token}"},
            params={"sort": "updated", "per_page": 50, "affiliation": "owner,collaborator"},
        )
        if reponse.status_code != 200:
            logging.error(f"ERREUR LISTE DEPOTS GITHUB (statut {reponse.status_code}) : {reponse.text[:200]}")
            return {"depots": [], "erreur": "Impossible de récupérer la liste des dépôts."}

        depots = [
            {
                "nom_complet": d["full_name"],
                "prive": d["private"],
                "description": d.get("description"),
                "url": d["html_url"],
            }
            for d in reponse.json()
        ]
        return {"depots": depots}
    except Exception as e:
        logging.error(f"ERREUR LISTE DEPOTS GITHUB : {e}")
        return {"depots": [], "erreur": "Impossible de récupérer la liste des dépôts."}
