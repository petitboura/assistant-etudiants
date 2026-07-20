"""
Génération d'archives de code (.zip) à partir d'un ou plusieurs fichiers.

Gratuit et local (juste de la compression, aucune clé API requise) : reste
actif dès maintenant, comme generation_documents.py. Réutilise le même
bucket Supabase "generations" (dossier "code/" au lieu de "documents/").
"""

import io
import logging
import uuid
import zipfile

from api.auth import supabase

BUCKET = "generations"


def generer_zip_depuis_fichiers(nom_projet: str, fichiers: dict[str, str]) -> str:
    """
    `fichiers` : dictionnaire {chemin_relatif: contenu_texte}, ex.
    {"main.py": "print('hello')", "README.md": "# Mon projet"}.

    Compresse tout en un .zip, l'upload dans Supabase Storage, renvoie
    l'URL publique. Même contrat d'erreur que generation_documents.py :
    les exceptions remontent telles quelles.
    """
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for chemin_fichier, contenu in fichiers.items():
            archive.writestr(chemin_fichier, contenu)
    tampon.seek(0)

    chemin_stockage = f"code/{uuid.uuid4()}-{nom_projet}.zip"
    try:
        supabase.storage.from_(BUCKET).upload(
            chemin_stockage, tampon.read(), {"content-type": "application/zip"}
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload code {chemin_stockage}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin_stockage)
