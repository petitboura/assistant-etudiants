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

NOTION (01/08, activation complète demandée par Bourama) : Notion NE suit
PAS le moteur générique ci-dessus -- DCR (RFC 7591) au lieu d'un client
fixe, ses propres tables (notion_oauth_temp/connexions_notion), voir
connexions/notion.py pour le detail et pourquoi. Ses fonctions
(demarrer_connexion_notion, etat_notion_en_attente,
finaliser_connexion_notion, est_connecte/obtenir_token_valide à un seul
argument user_id) existaient déjà mais n'étaient exposées sur AUCUNE
route -- personne ne pouvait se connecter à Notion depuis l'app. Plutôt
qu'un routeur séparé (qui obligerait le frontend à distinguer github et
notion), chaque handler générique ci-dessous fait un if service=="notion"
en tête et délègue à connexions/notion.py -- le frontend continue
d'appeler les mêmes /api/connexions/{service}/* pour les deux.
"""

import os
import re
import sys
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.erreurs import erreur_api
from connexions.oauth_generique import demarrer_connexion, est_connecte, etat_en_attente, finaliser_connexion, get_secret
import connexions.notion as notion

# mcp_tools.py fait un `from registre_outils import ...` interne (flat, pas
# `core.registre_outils`) -- ne se resout que si core/ est sur sys.path,
# meme contournement que api/agents.py (ligne 27 de ce fichier).
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
from mcp_tools import _appeler_outil_async  # noqa: E402

router = APIRouter(prefix="/api/connexions", tags=["connexions"])


@router.get("/{service}/statut")
def statut_connexion(service: str, utilisateur=Depends(utilisateur_courant)):
    if service == "notion":
        return {"connecte": notion.est_connecte(utilisateur.id)}
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
        raise erreur_api(404, "SERVICE_INCONNU", service=service)

    return {
        "client_id_present": bool(get_secret(config["client_id_env"])),
        "client_secret_present": bool(get_secret(config.get("client_secret_env", ""))),
        "url_retour_app": URL_RETOUR,
    }


@router.get("/{service}/demarrer")
def demarrer(service: str, agent_id: str = "", utilisateur=Depends(utilisateur_courant)):
    # Diagnostic précis (2026-07-23) : demarrer_connexion() ne renvoie que
    # None sans dire pourquoi -- ici on vérifie nous-mêmes chaque pièce
    # AVANT de l'appeler, pour remonter une erreur exploitable directement
    # dans l'alerte du bouton (pas besoin d'ouvrir une URL de diagnostic
    # séparée). Confirmé en test réel le 2026-07-23 : les 3 variables
    # Railway sont bien présentes, donc si ce message apparaît encore,
    # c'est très probablement que ce PROCESS-CI (peut-être un service
    # Railway différent de celui où les variables ont été ajoutées) ne
    # les voit pas -- pas un problème de configuration en soi.
    # Notion : pas de client_id fixe à vérifier (DCR, voir connexions/notion.py)
    # -- seule URL_RETOUR_APP est requise, demarrer_connexion_notion() gère
    # elle-même la découverte/l'enregistrement du client et logue l'erreur
    # précise si ça échoue.
    if service == "notion":
        from connexions.notion import URL_RETOUR as URL_RETOUR_NOTION

        if not URL_RETOUR_NOTION:
            raise erreur_api(
                503, "CONNEXION_INDISPONIBLE",
                message="Connexion notion indisponible : URL_RETOUR_APP absent(e) du PROCESS backend actuellement déployé.",
            )
        url = notion.demarrer_connexion_notion(utilisateur.id, agent_id or None)
        if not url:
            raise erreur_api(500, "CONNEXION_INDISPONIBLE", service=service)
        return {"url": url}

    from connexions.oauth_generique import SERVICES, URL_RETOUR

    config = SERVICES.get(service)
    if not config:
        raise erreur_api(404, "SERVICE_INCONNU", service=service)

    client_id = get_secret(config["client_id_env"])
    manques = []
    if not client_id:
        manques.append(config["client_id_env"])
    if not URL_RETOUR:
        manques.append("URL_RETOUR_APP")
    if manques:
        # Message de diagnostic volontairement technique (à l'attention du
        # créateur/développeur, pas d'un utilisateur final classique) :
        # surcharge le message par défaut du code plutôt que d'ajouter une
        # clé i18n pour un cas purement opérationnel.
        raise erreur_api(
            503,
            "CONNEXION_INDISPONIBLE",
            message=(
                f"Connexion {service} indisponible : {', '.join(manques)} absent(e) du "
                "PROCESS backend actuellement déployé (vérifie que ces variables sont bien "
                "sur le même service Railway que celui-ci, et qu'un redéploiement a eu lieu "
                "après leur ajout)."
            ),
        )

    url = demarrer_connexion(service, utilisateur.id, agent_id or None)
    if not url:
        raise erreur_api(500, "CONNEXION_INDISPONIBLE", service=service)
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
    if service:
        succes, message = finaliser_connexion(service, payload.code, payload.state)
        return {"succes": succes, "message": message, "service": service}

    # Pas trouvé dans oauth_temp (générique) -> tenter notion_oauth_temp,
    # même URL de callback partagée entre les deux systèmes (voir
    # connexions/notion.py:etat_notion_en_attente).
    if notion.etat_notion_en_attente(payload.state):
        succes, message = notion.finaliser_connexion_notion(payload.code, payload.state)
        return {"succes": succes, "message": message, "service": "notion"}

    return {"succes": False, "message": "Session de connexion expirée ou déjà utilisée.", "service": None}


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
        raise erreur_api(400, "GITHUB_NON_CONNECTE")

    try:
        reponse = requests.get(
            "https://api.github.com/user/repos",
            timeout=10,
            headers={"Authorization": f"Bearer {token}"},
            params={"sort": "updated", "per_page": 50, "affiliation": "owner,collaborator"},
        )
        if reponse.status_code != 200:
            logging.error(f"ERREUR LISTE DEPOTS GITHUB (statut {reponse.status_code}) : {reponse.text[:200]}")
            raise erreur_api(502, "GITHUB_DEPOTS_INDISPONIBLE")

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
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"ERREUR LISTE DEPOTS GITHUB : {e}")
        raise erreur_api(502, "GITHUB_DEPOTS_INDISPONIBLE")


@router.get("/notion/pages")
def pages_notion(q: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    Cherche des pages ET bases de données Notion visibles par la personne
    connectée. Voir BarreDeSaisie.tsx, sélecteur ouvert au clic sur "Notion"
    dans le menu Appli une fois connecté -- même pattern que
    depots_github ci-dessus, avec UNE différence de taille (voir plus bas).

    CORRECTION (01/08) : l'ancienne version appelait api.notion.com/v1/search
    en REST direct avec le token OAuth obtenu via le flow MCP (Dynamic Client
    Registration contre mcp.notion.com, voir connexions/notion.py). Ce token
    est valide pour le protocole MCP (audience mcp.notion.com) mais PAS pour
    l'API REST classique -- Notion renvoyait 401 "API token is invalid."
    systématiquement (confirmé en logs Railway le 01/08 : l'appel MCP juste
    avant réussissait, l'appel REST juste après échouait avec le même token).
    Corrigé en passant par le même mécanisme MCP déjà fonctionnel
    (core/mcp_tools.py:_appeler_outil_async), en appelant l'outil
    notion-search du serveur MCP au lieu de l'API REST.

    DIFFÉRENCE AVEC GITHUB : notion-search est une recherche sémantique, pas
    un listing -- son paramètre `query` est OBLIGATOIRE côté outil (pas de
    "toutes mes pages" comme /user/repos pour GitHub). Sans texte tapé par
    la personne, on renvoie donc une liste vide plutôt que d'inventer une
    requête -- le sélecteur Notion a besoin d'une vraie zone de recherche
    côté frontend (contrairement au sélecteur GitHub qui liste tout
    directement), voir BarreDeSaisie.tsx.
    """
    if not q.strip():
        return {"pages": []}

    token = notion.obtenir_token_valide(utilisateur.id)
    if not token:
        raise erreur_api(400, "NOTION_NON_CONNECTE")

    try:
        resultat_brut = asyncio.run(
            _appeler_outil_async(
                "https://mcp.notion.com/mcp",
                "notion-search",
                {"query": q, "page_size": 20},
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        donnees = json.loads(resultat_brut)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"ERREUR RECHERCHE NOTION (MCP) : {e}")
        raise erreur_api(502, "NOTION_PAGES_INDISPONIBLE")

    pages = [
        {
            "titre": r.get("title") or "(sans titre)",
            "type": "database" if r.get("type") == "database" else "page",
            "url": r.get("url"),
        }
        for r in (donnees.get("results") or [])
        if r.get("url")
    ]
    return {"pages": pages}


@router.get("/notion/bases/lignes")
def lignes_base_notion(url: str, q: str = "", utilisateur=Depends(utilisateur_courant)):
    """
    Interroge le CONTENU d'une base Notion (02/08, demande Bourama : "on va
    ajouter" notion-query-data-sources / notion-query-database-view --
    scope confirmé : choisir la base via le sélecteur existant [type
    "database" dans /notion/pages ci-dessus], puis un 2e écran de requête).

    `url` = l'URL Notion de la base (celle renvoyée par /notion/pages, type
    "database"), PAS une data source URL -- c'est cette route qui fait la
    conversion, voir ci-dessous.

    ÉTAPE 1 -- notion-fetch(url) : une base Notion n'est pas directement
    interrogeable par son URL de page. Il faut d'abord la "fetcher" pour
    obtenir sa data source URL (collection://<uuid>), qui apparaît dans le
    Markdown renvoyé sous forme de balise <data-source url="collection://...">
    (voir doc de l'outil notion-fetch). D'où l'appel préalable ici.

    ÉTAPE 2 -- notion-query-data-sources en mode SQL : `SELECT * FROM
    "<data_source_url>" LIMIT 50`. Mode SQL choisi plutôt que le mode "view"
    (notion-query-database-view) parce que ce dernier a besoin d'une URL de
    VUE (?v=...), qu'on n'est pas garanti d'obtenir facilement depuis le
    fetch -- alors que la data source URL, elle, est toujours présente.

    `q` (le texte tapé dans le 2e écran) : pas de vraie clause SQL dynamique
    construite à partir du texte de la personne (schéma de colonnes inconnu
    à l'avance, risque d'injection si mal fait) -- on charge les lignes
    (max 50) puis on filtre nous-mêmes en Python sur toutes les valeurs de
    chaque ligne. Suffisant pour un sélecteur rapide, pas pour une vraie
    recherche structurée.
    """
    token = notion.obtenir_token_valide(utilisateur.id)
    if not token:
        raise erreur_api(400, "NOTION_NON_CONNECTE")

    entetes = {"Authorization": f"Bearer {token}"}

    try:
        fetch_brut = asyncio.run(
            _appeler_outil_async(
                "https://mcp.notion.com/mcp", "notion-fetch", {"id": url}, headers=entetes
            )
        )
    except Exception as e:
        logging.error(f"ERREUR FETCH BASE NOTION (MCP) : {e}")
        raise erreur_api(502, "NOTION_BASE_INDISPONIBLE")

    correspondance = re.search(r'collection://[0-9a-fA-F-]+', fetch_brut)
    if not correspondance:
        raise erreur_api(404, "NOTION_BASE_SANS_DATA_SOURCE")
    data_source_url = correspondance.group(0)

    try:
        query_brut = asyncio.run(
            _appeler_outil_async(
                "https://mcp.notion.com/mcp",
                "notion-query-data-sources",
                {
                    "data": {
                        "mode": "sql",
                        "data_source_urls": [data_source_url],
                        "query": f'SELECT * FROM "{data_source_url}" LIMIT 50',
                    }
                },
                headers=entetes,
            )
        )
        donnees = json.loads(query_brut)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"ERREUR REQUETE BASE NOTION (MCP) : {e}")
        raise erreur_api(502, "NOTION_BASE_INDISPONIBLE")

    lignes_brutes = donnees.get("rows") or donnees.get("results") or []
    q_normalise = q.strip().lower()

    lignes = []
    for ligne in lignes_brutes:
        if not isinstance(ligne, dict):
            continue
        # Nom de la colonne "titre" inconnu à l'avance (schéma variable
        # d'une base à l'autre) -- on prend la première valeur texte non
        # vide comme titre d'affichage, plutôt que de deviner un nom de
        # colonne fixe ("Nom", "Name", "Titre"...).
        titre = next(
            (str(v) for v in ligne.values() if isinstance(v, str) and v.strip()),
            "(sans titre)",
        )
        url_ligne = ligne.get("url") or ligne.get("Url") or ligne.get("URL")
        if q_normalise and not any(
            q_normalise in str(v).lower() for v in ligne.values() if v is not None
        ):
            continue
        lignes.append({"titre": titre, "url": url_ligne, "proprietes": ligne})

    return {"lignes": lignes}


class CreationPageNotion(BaseModel):
    titre: str
    contenu: str = ""


@router.post("/notion/pages")
def creer_page_notion(payload: CreationPageNotion, utilisateur=Depends(utilisateur_courant)):
    """
    Crée une page Notion standalone (02/08, demande Bourama, scope confirmé :
    titre + zone de texte pour le contenu, pas de choix de page/base parente
    dans cette itération -> pages créées au niveau racine du workspace,
    l'utilisateur les range ensuite lui-même dans Notion s'il veut).

    ATTENTION -- notion-create-pages est listé dans OUTILS_SENSIBLES
    (core/registre_outils.py) : quand le LLM appelle cet outil PENDANT une
    conversation, main.py interrompt le flux pour demander confirmation.
    CETTE route-ci ne passe PAS par main.py (même schéma que /notion/pages
    en GET et /notion/bases/lignes ci-dessus : appel MCP direct depuis le
    sélecteur) -- la confirmation ici, c'est le clic explicite de la
    personne sur "Créer" dans le formulaire du sélecteur, pas une
    interruption supplémentaire côté backend.
    """
    if not payload.titre.strip():
        raise erreur_api(400, "TITRE_REQUIS")

    token = notion.obtenir_token_valide(utilisateur.id)
    if not token:
        raise erreur_api(400, "NOTION_NON_CONNECTE")

    try:
        resultat_brut = asyncio.run(
            _appeler_outil_async(
                "https://mcp.notion.com/mcp",
                "notion-create-pages",
                {
                    "pages": [
                        {
                            "properties": {"title": payload.titre},
                            "content": payload.contenu,
                        }
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        donnees = json.loads(resultat_brut)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"ERREUR CREATION PAGE NOTION (MCP) : {e}")
        raise erreur_api(502, "NOTION_CREATION_INDISPONIBLE")

    pages_creees = donnees.get("pages") or donnees.get("results") or []
    url_page = pages_creees[0].get("url") if pages_creees else None
    if not url_page:
        raise erreur_api(502, "NOTION_CREATION_INDISPONIBLE")

    return {"url": url_page}
