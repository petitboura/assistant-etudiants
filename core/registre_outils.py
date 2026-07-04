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

Chaque *_builder est une fonction qui recoit get_secret (pour aller lire
les cles API dans les secrets Streamlit / variables d'environnement) et
retourne soit une URL (str), soit des headers (dict).
"""


def _url_tavily(get_secret):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


def _url_wolfram(get_secret):
    # Wolfram MCP Service ne demande plus de cle API (verifie sur la page
    # officielle wolfram.com/artificial-intelligence/mcp-service : "API
    # keys are no longer required to access Wolfram MCP Service").
    # A surveiller : pas de cle = potentiel rate-limit anonyme par IP.
    return "https://services.wolfram.com/api/mcp"


SERVEURS_MCP = [
    {"nom": "tavily", "url_builder": _url_tavily},
    {"nom": "wolfram", "url_builder": _url_wolfram},
]
