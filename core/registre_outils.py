"""
Registre des outils (bras) MCP actifs.

POUR AJOUTER UN NOUVEL OUTIL :
Ajoute une entree dans SERVEURS_MCP ci-dessous. C'est le seul fichier a
modifier. Ni mcp_tools.py (le moteur generique) ni main.py n'ont besoin
d'etre touches.

Deux modes d'authentification sont supportes, car les serveurs MCP ne
s'authentifient pas tous pareil :
- pas de cle du tout (ex: Wolfram)          -> url_builder seul
- cle glissee dans l'URL (ex: Tavily)       -> url_builder seul
- cle envoyee en header HTTP (si besoin un jour) -> url_builder + headers_builder

Chaque *_builder est une fonction qui recoit (get_secret, user_id) et
retourne soit une URL (str), soit des headers (dict), soit None. Le
parametre user_id est ignore par la plupart des outils (cle API globale,
comme Tavily/Wolfram) ; il n'est utile que pour un outil "par utilisateur"
(cle "necessite_utilisateur": True), ou chaque etudiant connecte son
propre compte plutot que d'utiliser une cle partagee par toute l'app.

POUR UN OUTIL "PAR UTILISATEUR" (ex: Notion) :
Ajoute "necessite_utilisateur": True dans son entree. Le dispatcher
(mcp_tools.py) l'ignore alors automatiquement si aucun etudiant n'est
connecte a l'app, ou si headers_builder renvoie None (etudiant connecte a
l'app mais pas encore a CET outil) -> pas de bloc if/else a ecrire ici.
"""

from oauth_notion import obtenir_token_valide


def _url_tavily(get_secret, user_id):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


def _url_wolfram(get_secret, user_id):
    # Wolfram MCP Service ne demande plus de cle API (verifie sur la page
    # officielle wolfram.com/artificial-intelligence/mcp-service : "API
    # keys are no longer required to access Wolfram MCP Service").
    # A surveiller : pas de cle = potentiel rate-limit anonyme par IP.
    return "https://services.wolfram.com/api/mcp"


def _url_notion(get_secret, user_id):
    return "https://mcp.notion.com/mcp"


def _headers_notion(get_secret, user_id):
    token = obtenir_token_valide(user_id)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


SERVEURS_MCP = [
    {"nom": "tavily", "url_builder": _url_tavily},
    {"nom": "wolfram", "url_builder": _url_wolfram},
    {
        "nom": "notion",
        "url_builder": _url_notion,
        "headers_builder": _headers_notion,
        "necessite_utilisateur": True,
    },
]
