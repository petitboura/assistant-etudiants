"""
Moteur MCP generique.

Ce fichier ne contient plus la liste des outils : elle vit dans
registre_outils.py (SERVEURS_MCP). Pour ajouter un nouvel outil, va
modifier ce fichier-la, pas celui-ci.

Comment ca marche : chaque serveur MCP sait decrire lui-meme les outils
qu'il expose (list_tools). Ce fichier se contente de demander cette liste
a chaque serveur configure dans le registre ET autorise pour l'agent
courant (agents.tools_enabled), de la transformer au format que Groq
comprend, et de savoir rappeler le bon serveur (avec la bonne URL et les
bons headers) quand Groq demande a executer un outil.
"""

import os
import asyncio
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from supabase import create_client

from registre_outils import SERVEURS_MCP

logging.basicConfig(level=logging.INFO)


def _get_secret_local(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


_supabase = create_client(_get_secret_local("SUPABASE_URL"), _get_secret_local("SUPABASE_SECRET"))


def _outils_actives_pour_agent(agent_id):
    """
    Retourne la liste des noms de serveurs MCP (ex: ["wolfram", "notion"])
    autorises pour cet agent, d'apres agents.tools_enabled.

    Par defaut restrictif : si la colonne est vide/absente, ou si la
    requete Supabase echoue, on retourne une liste vide plutot que "tous
    les outils" -> un agent mal configure n'a AUCUN outil au lieu d'en
    heriter silencieusement d'un autre. Force une config explicite par
    agent, conformement au choix pris pour ce chantier.
    """
    if not agent_id:
        logging.error("_outils_actives_pour_agent appelé sans agent_id : aucun outil activé.")
        return []
    try:
        res = (
            _supabase.table("agents")
            .select("tools_enabled")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("tools_enabled") or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents.tools_enabled, agent_id={agent_id}) : {e}")
        return []


async def _lister_outils_async(url, headers=None):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            reponse = await session.list_tools()
            return reponse.tools


async def _appeler_outil_async(url, nom_outil, arguments, headers=None):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resultat = await session.call_tool(nom_outil, arguments=arguments)
            for bloc in resultat.content:
                if hasattr(bloc, "text"):
                    return bloc.text
    return ""


def lister_tous_les_outils(get_secret, user_id=None, agent_id=None):
    """
    Se connecte a chaque serveur MCP du registre AUTORISE POUR CET AGENT
    (voir agents.tools_enabled) et retourne :
    - outils_pour_llm : la liste des outils au format attendu par l'API
      Groq (parametre tools=...), pour que le modele decide seul s'il
      les utilise
    - table_routage : un dictionnaire {nom_outil: {"url":..., "headers":...}},
      pour pouvoir rappeler le bon serveur plus tard (avec la bonne
      authentification) sans aucun if/else en dur

    `agent_id` determine quels serveurs de SERVEURS_MCP sont meme
    interroges (filtre AVANT tout appel reseau) : un agent sans
    tools_enabled configure n'a acces a AUCUN outil, par defaut restrictif
    (voir _outils_actives_pour_agent). Ajouter/retirer un outil pour un
    agent = modifier agents.tools_enabled en base, jamais ce fichier.

    `user_id` et `agent_id` sont transmis a chaque url_builder/
    headers_builder. La plupart les ignorent (cle API globale, ex:
    Tavily, Wolfram) ; certains outils "par utilisateur" (ex: Notion) en
    ont besoin pour aller chercher le bon token. Notion specifiquement
    scope sa connexion par (user_id, agent_id) et non user_id seul
    (Option A, juillet 2026) : un etudiant connecte a Notion pour un
    agent n'est PAS automatiquement connecte pour un autre -> voir
    connexions/notion.py. Si un outil necessite un utilisateur et qu'aucun
    n'est connecte (ou pas encore connecte a CET outil POUR CET AGENT), il
    est ignore silencieusement : il n'apparait simplement pas dans la
    liste proposee au modele.
    """
    outils_pour_llm = []
    table_routage = {}

    noms_serveurs_actives = _outils_actives_pour_agent(agent_id)
    serveurs_pour_cet_agent = [s for s in SERVEURS_MCP if s["nom"] in noms_serveurs_actives]

    logging.info(
        f"Agent '{agent_id}' -> serveurs MCP activés : {noms_serveurs_actives or '(aucun)'} "
        f"({len(serveurs_pour_cet_agent)}/{len(SERVEURS_MCP)} du registre retenus)"
    )

    for serveur in serveurs_pour_cet_agent:
        nom = serveur["nom"]
        try:
            if serveur.get("necessite_utilisateur") and not user_id:
                logging.info(f"MCP '{nom}' ignoré : nécessite un utilisateur connecté, aucun user_id fourni.")
                continue

            url = serveur["url_builder"](get_secret, user_id, agent_id)
            headers = serveur["headers_builder"](get_secret, user_id, agent_id) if "headers_builder" in serveur else None

            if serveur.get("necessite_utilisateur") and headers is None:
                logging.info(f"MCP '{nom}' ignoré : utilisateur {user_id} pas connecté à cet outil POUR L'AGENT '{agent_id}' (headers=None).")
                continue

            outils = asyncio.run(_lister_outils_async(url, headers))

            outils_autorises = serveur.get("outils_autorises")
            if outils_autorises is not None:
                outils = [o for o in outils if o.name in outils_autorises]

            noms_outils = [o.name for o in outils]
            logging.info(f"MCP '{nom}' -> {len(outils)} outil(s) listé(s) : {noms_outils}")
            for outil in outils:
                outils_pour_llm.append({
                    "type": "function",
                    "function": {
                        "name": outil.name,
                        "description": outil.description or "",
                        "parameters": outil.inputSchema,
                    },
                })
                table_routage[outil.name] = {"url": url, "headers": headers}
        except Exception as e:
            logging.error(f"ERREUR MCP listing ({nom}): {e}")

    logging.info(f"Outils envoyés au LLM ce tour-ci : {[o['function']['name'] for o in outils_pour_llm]}")
    return outils_pour_llm, table_routage


def appeler_outil(nom_outil, arguments, table_routage):
    """
    Execute un outil par son nom, quel que soit le serveur MCP qui
    l'expose. Le routage (URL + headers) se fait automatiquement via
    table_routage, construite par lister_tous_les_outils().
    """
    logging.info(f"Appel outil demandé par le LLM : {nom_outil}({arguments})")
    route = table_routage.get(nom_outil)
    if not route:
        logging.error(f"Outil '{nom_outil}' demandé par le LLM mais absent de la table de routage.")
        return f"Erreur : outil '{nom_outil}' inconnu."
    try:
        resultat = asyncio.run(
            _appeler_outil_async(route["url"], nom_outil, arguments, route.get("headers"))
        )
        logging.info(f"Résultat outil '{nom_outil}' : {len(resultat or '')} caractères")
        return resultat
    except Exception as e:
        logging.error(f"ERREUR MCP appel a {nom_outil}: {e}")
        return f"Erreur lors de l'appel a l'outil '{nom_outil}'."
