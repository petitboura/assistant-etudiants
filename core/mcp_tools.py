"""
Moteur MCP generique.

POUR AJOUTER UN NOUVEL OUTIL PLUS TARD :
Ajoute une entree dans SERVEURS_MCP ci-dessous (nom + comment construire
l'URL de connexion). C'est tout. Aucun autre fichier du projet n'a besoin
d'etre modifie, ni main.py ni faces/app_etudiant.py.

Comment ca marche : chaque serveur MCP sait decrire lui-meme les outils
qu'il expose (list_tools). Ce fichier se contente de demander cette liste
a chaque serveur configure, de la transformer au format que Groq comprend,
et de savoir rappeler le bon serveur quand Groq demande a executer un outil.
"""

import asyncio
import logging

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _url_tavily(get_secret):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


# Registre des serveurs MCP actifs.
# Nouvel outil = nouvelle entree ici, rien d'autre.
SERVEURS_MCP = [
    {"nom": "tavily", "url_builder": _url_tavily},
]


async def _lister_outils_async(url):
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            reponse = await session.list_tools()
            return reponse.tools


async def _appeler_outil_async(url, nom_outil, arguments):
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            resultat = await session.call_tool(nom_outil, arguments=arguments)
            for bloc in resultat.content:
                if hasattr(bloc, "text"):
                    return bloc.text
    return ""


def lister_tous_les_outils(get_secret):
    """
    Se connecte a chaque serveur MCP du registre et retourne :
    - outils_pour_llm : la liste des outils au format attendu par l'API
      Groq (parametre tools=...), pour que le modele decide seul s'il
      les utilise
    - table_routage : un dictionnaire {nom_outil: url_du_serveur}, pour
      pouvoir rappeler le bon serveur plus tard sans aucun if/else en dur
    """
    outils_pour_llm = []
    table_routage = {}

    for serveur in SERVEURS_MCP:
        try:
            url = serveur["url_builder"](get_secret)
            outils = asyncio.run(_lister_outils_async(url))
            for outil in outils:
                outils_pour_llm.append({
                    "type": "function",
                    "function": {
                        "name": outil.name,
                        "description": outil.description or "",
                        "parameters": outil.inputSchema,
                    },
                })
                table_routage[outil.name] = url
        except Exception as e:
            logging.error(f"ERREUR MCP listing ({serveur['nom']}): {e}")

    return outils_pour_llm, table_routage


def appeler_outil(nom_outil, arguments, table_routage):
    """
    Execute un outil par son nom, quel que soit le serveur MCP qui
    l'expose. Le routage se fait automatiquement via table_routage,
    construite par lister_tous_les_outils().
    """
    url = table_routage.get(nom_outil)
    if not url:
        return f"Erreur : outil '{nom_outil}' inconnu."
    try:
        return asyncio.run(_appeler_outil_async(url, nom_outil, arguments))
    except Exception as e:
        logging.error(f"ERREUR MCP appel a {nom_outil}: {e}")
        return f"Erreur lors de l'appel a l'outil '{nom_outil}'."