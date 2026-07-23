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

Chaque *_builder est une fonction qui recoit (get_secret, user_id, agent_id)
et retourne soit une URL (str), soit des headers (dict), soit None. Les
parametres user_id/agent_id sont ignores par la plupart des outils (cle
API globale, comme Tavily/Wolfram) ; ils ne sont utiles que pour un outil
"par utilisateur" (cle "necessite_utilisateur": True), ou chaque etudiant
connecte son propre compte plutot que d'utiliser une cle partagee par
toute l'app. Pour Notion specifiquement, la connexion est scopee par
user_id seul (compte unifie, juillet 2026) : un etudiant connecte a
Notion depuis n'importe quel agent l'est automatiquement pour tous les
autres agents de la plateforme -> voir connexions/notion.py.

POUR UN OUTIL "PAR UTILISATEUR" (ex: Notion) :
Ajoute "necessite_utilisateur": True dans son entree. Le dispatcher
(mcp_tools.py) l'ignore alors automatiquement si aucun etudiant n'est
connecte a l'app, ou si headers_builder renvoie None (etudiant connecte a
l'app mais pas encore a CET outil POUR CET AGENT) -> pas de bloc if/else
a ecrire ici.
"""

import os

from connexions.notion import obtenir_token_valide

def _url_generation(get_secret, user_id, agent_id):
    # Serveur MCP interne, pas un tiers externe (voir
    # core/serveur_mcp_generation.py, monté dans api/main.py). C'est
    # TOUJOURS le même process/port que celui qui répond à cette
    # requête (localhost, jamais un vrai domaine externe), donc pas
    # besoin de BACKEND_URL ici : on lit directement $PORT, la variable
    # que Railway fournit et qu'uvicorn utilise pour écouter. Coder
    # "8000" en dur ici serait faux dès que Railway attribue un autre
    # port -- c'est exactement le bug qui a empêché l'outil de
    # fonctionner au premier test (2026-07-20).
    port = os.environ.get("PORT", "8000")
    return f"http://localhost:{port}/mcp/generation"


def _url_github(get_secret, user_id, agent_id):
    # Même logique que _url_generation ci-dessus -- serveur MCP interne
    # (core/serveur_mcp_github.py), pas un tiers externe.
    port = os.environ.get("PORT", "8000")
    return f"http://localhost:{port}/mcp/github"


def _url_tavily(get_secret, user_id, agent_id):
    return f"https://mcp.tavily.com/mcp/?tavilyApiKey={get_secret('TAVILY_API_KEY')}"


def _url_wolfram(get_secret, user_id, agent_id):
    # Wolfram MCP Service ne demande plus de cle API (verifie sur la page
    # officielle wolfram.com/artificial-intelligence/mcp-service : "API
    # keys are no longer required to access Wolfram MCP Service").
    # A surveiller : pas de cle = potentiel rate-limit anonyme par IP.
    return "https://services.wolfram.com/api/mcp"


def _url_notion(get_secret, user_id, agent_id):
    return "https://mcp.notion.com/mcp"


def _headers_notion(get_secret, user_id, agent_id):
    # agent_id fait partie de la signature commune a tous les *_builder
    # (voir docstring en tete de fichier) mais n'est plus utilise ici :
    # la connexion Notion est scopee par user_id seul (compte unifie).
    token = obtenir_token_valide(user_id)
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


SERVEURS_MCP = [
    {"nom": "wolfram", "url_builder": _url_wolfram},
    {
        "nom": "generation",
        "url_builder": _url_generation,
        # Pas de "outils_autorises" fixe ici : categorie 1, filtree
        # dynamiquement par agent (agents_outils_generation croise avec
        # registre_outils_plateforme.disponible), voir mcp_tools.py ->
        # _outils_generation_actifs_pour_agent. Ce serveur est TOUJOURS
        # interroge (voir lister_tous_les_outils), contrairement a
        # wolfram/github/notion qui dependent de agents_serveurs.
    },
    {
        "nom": "github",
        "url_builder": _url_github,
        # explorer_depot_github et lire_fichier_depot_github sont sans
        # risque (lecture seule). modifier_fichier_depot_github ÉCRIT
        # réellement sur un dépôt -> dans OUTILS_SENSIBLES plus bas,
        # donc TOUJOURS interrompu pour confirmation avant exécution,
        # quel que soit le mode (direct ou branche+PR).
    },
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
        "outils_autorises": ["notion-search"],
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
    # ÉCRIT réellement sur un dépôt GitHub (voir
    # core/serveur_mcp_github.py, modifier_fichier_depot_github) --
    # TOUJOURS interrompu pour confirmation, que ce soit en mode "direct"
    # (commit sur la branche de base) ou "branche_pr" (nouvelle branche +
    # Pull Request). Aucun des deux modes n'est silencieux.
    "modifier_fichier_depot_github",
}
