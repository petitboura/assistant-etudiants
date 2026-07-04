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

from connexions.notion import obtenir_token_valide

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
    {"nom": "wolfram", "url_builder": _url_wolfram},
    {
        "nom": "notion",
        "url_builder": _url_notion,
        "headers_builder": _headers_notion,
        "necessite_utilisateur": True,
        # Notion expose 20 outils (creation/edition de pages, bases de
        # donnees, commentaires, equipes...) dont la description JSON
        # complete depasse a elle seule la limite de tokens/minute du
        # tier Groq gratuit (8000 TPM) une fois cumulee avec Tavily ->
        # 413 Payload Too Large systematique, qui faisait basculer sur
        # le fallback Gemini SANS AUCUN outil (ni Notion ni Tavily).
        # Un etudiant n'a besoin que de consulter son Notion, pas de le
        # modifier -> on ne garde que les outils de lecture pour l'instant.
        "outils_autorises": ["notion-search", "notion-fetch"],
    },
]

# Outils qui MODIFIENT reellement quelque chose chez l'etudiant (creation,
# edition, suppression, deplacement...). main.py interrompt le flux et
# demande une confirmation explicite avant d'executer l'un de ces outils,
# quel que soit le serveur MCP dont il provient. Pour l'instant aucun
# outil d'ecriture n'est dans `outils_autorises` ci-dessus (donc cette
# liste n'a pas encore d'effet visible) : elle sert de garde-fou pret a
# l'emploi le jour ou on active par ex. "notion-create-pages".
OUTILS_SENSIBLES = {
    "notion-create-pages",
    "notion-update-page",
    "notion-move-pages",
    "notion-duplicate-page",
    "notion-create-database",
    "notion-update-data-source",
    "notion-create-comment",
    "notion-create-view",
    "notion-update-view",
    "notion-create-attachment",
}
