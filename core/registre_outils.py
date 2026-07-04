"""
Registre des outils (bras) MCP actifs.

POUR AJOUTER UN NOUVEL OUTIL :
Ajoute une entree dans SERVEURS_MCP ci-dessous. C'est le seul fichier a
modifier. Ni mcp_tools.py (le moteur generique) ni main.py n'ont besoin
d'etre touches.

Deux modes d'authentification sont supportes, car les serveurs MCP ne
s'authentifient pas tous pareil :
- cle glissee dans l'URL (ex: Tavily)      -> url_builder seul
- cle envoyee en header HTTP (ex: Wolfram) -> url_builder + headers_builder

Chaque *_builder est une fonction qui recoit get_secret (pour aller lire
les cles API dans les secrets Streamlit / variables d'environnement) et
retourne soit une URL (str), soit des headers (dict).
"""


def _url_tavily(get_secret):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


def _url_wolfram(get_secret):
    return "https://services.wolfram.com/api/mcp"


def _headers_wolfram(get_secret):
    return {"Authorization": f"Bearer {get_secret('WOLFRAM_API_KEY')}"}


SERVEURS_MCP = [
    {"nom": "tavily", "url_builder": _url_tavily},
    {"nom": "wolfram", "url_builder": _url_wolfram, "headers_builder": _headers_wolfram},
]
