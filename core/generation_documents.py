"""
Génération de documents PDF à partir de texte/markdown.

Gratuit et local (WeasyPrint convertit du HTML/CSS en PDF, aucune clé API
requise) : contrairement à generation_images.py, cette fonctionnalité peut
rester active dès maintenant, elle ne coûte rien à faire tourner.

Flux : markdown -> HTML (lib `markdown`, déjà dans requirements.txt) ->
PDF (WeasyPrint) -> upload Supabase Storage -> URL publique renvoyée.

Prérequis Supabase à créer une fois, à la main, avant la première
utilisation (voir README_GENERATION.md) : un bucket public nommé
"generations", pas encore créé automatiquement par ce code.
"""

import logging
import uuid

import markdown as md_lib
from weasyprint import HTML

from api.auth import supabase

BUCKET = "generations"

# Feuille de style minimale, volontairement sobre : Bourama pourra
# l'enrichir plus tard (logo Djiguignè, couleurs Maame) sans toucher à la
# logique de génération elle-même.
CSS_DE_BASE = """
@page { margin: 2.5cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 20pt; margin-bottom: 0.3em; }
h2 { font-size: 15pt; margin-top: 1.2em; }
h3 { font-size: 12.5pt; margin-top: 1em; }
code { background: #f2f2f2; padding: 1px 4px; border-radius: 3px; }
pre { background: #f2f2f2; padding: 10px; border-radius: 5px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #ddd; padding: 6px 10px; }
"""


def generer_pdf_depuis_markdown(titre: str, contenu_markdown: str) -> str:
    """
    Convertit du markdown en PDF, l'upload dans Supabase Storage, renvoie
    l'URL publique.

    Lève une exception si WeasyPrint ou l'upload échoue -- à l'appelant
    (serveur MCP ou route REST) de transformer ça en message utilisateur
    clair, pas de logique de message d'erreur ici.
    """
    html_corps = md_lib.markdown(contenu_markdown, extensions=["tables", "fenced_code"])
    html_complet = f"""
    <html>
      <head><meta charset="utf-8"><style>{CSS_DE_BASE}</style></head>
      <body><h1>{titre}</h1>{html_corps}</body>
    </html>
    """

    pdf_bytes = HTML(string=html_complet).write_pdf()

    chemin = f"documents/{uuid.uuid4()}.pdf"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin, pdf_bytes, {"content-type": "application/pdf"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload document {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
