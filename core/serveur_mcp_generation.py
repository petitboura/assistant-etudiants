"""
Serveur MCP local (documents / code / images), monté directement dans
l'API FastAPI existante (voir api/main.py) -- pas un service Railway
séparé, pas de déploiement supplémentaire à gérer.

Pourquoi un serveur MCP plutôt que d'appeler generation_*.py directement
dans core/main.py : pour rester cohérent avec registre_outils.py, qui
documente explicitement "pour ajouter un nouvel outil, ajoute une entrée
dans SERVEURS_MCP, ni mcp_tools.py ni main.py n'ont besoin d'être
touchés". Ce fichier-ci EST le nouveau serveur qu'on enregistre là-bas,
au même titre que Wolfram/Tavily/Notion, sauf qu'il tourne chez nous au
lieu d'être hébergé par un tiers.

Génération d'image (generer_image) n'est exposée QUE si
TOGETHER_API_KEY est configurée (voir generation_images.py,
image_generation_disponible()) : tant que Bourama n'a pas les moyens de
payer Together AI, l'agent ne voit tout simplement pas cet outil dans la
liste -- pas de risque qu'il essaie de l'appeler et échoue en pleine
conversation avec un étudiant.
"""

from mcp.server.fastmcp import FastMCP

from core.generation_documents import generer_pdf_depuis_markdown
from core.generation_code import generer_zip_depuis_fichiers
from core.generation_donnees import exporter_donnees as _exporter_donnees
from core.generation_signature import (
    envoyer_pour_signature as _envoyer_pour_signature,
    statut_signature as _statut_signature,
    signature_disponible,
)
from core.generation_images import generer_image as _generer_image, image_generation_disponible

mcp_generation = FastMCP(
    name="generation",
    stateless_http=True,
    streamable_http_path="/",
)


@mcp_generation.tool()
def generer_document(titre: str, contenu_markdown: str) -> str:
    """
    Génère un document PDF à partir d'un titre et d'un contenu au format
    markdown (titres, listes, tableaux, blocs de code supportés).
    Renvoie l'URL publique du PDF généré, prête à être partagée à
    l'étudiant.
    """
    try:
        return generer_pdf_depuis_markdown(titre, contenu_markdown)
    except Exception:
        return "Erreur : la génération du document a échoué, réessaie."


@mcp_generation.tool()
def generer_code(nom_projet: str, fichiers: dict) -> str:
    """
    Génère une archive .zip téléchargeable à partir d'un ou plusieurs
    fichiers de code. `fichiers` est un dictionnaire {chemin: contenu},
    ex. {"main.py": "print('hello')"}. Renvoie l'URL publique du .zip.
    """
    try:
        return generer_zip_depuis_fichiers(nom_projet, fichiers)
    except Exception:
        return "Erreur : la génération de l'archive a échoué, réessaie."


@mcp_generation.tool()
def exporter_donnees(nom: str, donnees: dict, format: str = "json") -> str:
    """
    Exporte des données structurées (un dictionnaire, potentiellement
    imbriqué) vers un fichier JSON ou XML téléchargeable. `format` doit
    valoir "json" ou "xml". Renvoie l'URL publique du fichier généré.
    """
    try:
        return _exporter_donnees(nom, donnees, format)
    except Exception:
        return "Erreur : l'export des données a échoué, réessaie."


# Enregistré conditionnellement, même logique que generer_image ci-dessous :
# LUMIN_API_KEY absente -> l'agent ne voit tout simplement pas ces outils.
if signature_disponible():
    @mcp_generation.tool()
    def envoyer_pour_signature(titre: str, contenu_markdown: str, signataires: list) -> str:
        """
        Génère un document PDF à partir d'un contenu markdown et
        l'envoie pour signature électronique (via Lumin) à un ou
        plusieurs signataires. `signataires` : liste de
        {"nom": ..., "email": ...}. Chaque signataire reçoit un email
        avec un lien pour signer. Renvoie l'identifiant de la demande
        de signature et son statut.
        """
        try:
            resultat = _envoyer_pour_signature(titre, contenu_markdown, signataires)
            return (
                f"Demande de signature envoyée (id: {resultat['signature_request_id']}, "
                f"statut: {resultat['statut']}). Document : {resultat['url_document']}"
            )
        except Exception:
            return "Erreur : l'envoi pour signature a échoué, réessaie."

    @mcp_generation.tool()
    def consulter_statut_signature(signature_request_id: str) -> str:
        """
        Consulte l'état d'une demande de signature déjà envoyée
        (en attente, signé, expiré...).
        """
        try:
            return str(_statut_signature(signature_request_id))
        except Exception:
            return "Erreur : impossible de récupérer le statut, vérifie l'identifiant."


# Enregistré conditionnellement (pas de decorateur @mcp_generation.tool()
# direct) : image_generation_disponible() est vérifiée à l'IMPORT de ce
# module, une seule fois au démarrage du process, pas à chaque requête --
# cohérent avec le fait qu'ajouter la clé Together AI nécessite de toute
# façon un redéploiement Railway (donc un nouveau démarrage du process).
if image_generation_disponible():
    @mcp_generation.tool()
    def generer_image(prompt: str) -> str:
        """
        Génère une image à partir d'une description textuelle (Flux
        Schnell / Together AI). Renvoie l'URL publique de l'image
        générée.
        """
        try:
            return _generer_image(prompt)
        except Exception:
            return "Erreur : la génération de l'image a échoué, réessaie."
