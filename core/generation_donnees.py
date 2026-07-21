"""
Export de données structurées (JSON ou XML) vers un fichier
téléchargeable.

Gratuit et local, même famille que generation_documents.py et
generation_code.py : pas de clé API, reste actif dès le déploiement.
Réutilise le bucket Supabase "generations" (dossier "donnees/").
"""

import json
import logging
import uuid
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from api.auth import supabase

BUCKET = "generations"


def _dict_vers_xml(nom_racine: str, donnees) -> Element:
    """
    Convertit récursivement un dict/list/valeur simple en arbre XML.
    Volontairement minimaliste (pas de dépendance externe type
    dicttoxml) : suffisant pour des exports de données simples, pas pour
    des schémas XML avec espaces de noms ou attributs complexes.
    """
    element = Element(nom_racine)
    if isinstance(donnees, dict):
        for cle, valeur in donnees.items():
            enfant = _dict_vers_xml(str(cle), valeur)
            element.append(enfant)
    elif isinstance(donnees, list):
        for item in donnees:
            enfant = _dict_vers_xml("element", item)
            element.append(enfant)
    else:
        element.text = str(donnees)
    return element


def exporter_donnees(nom: str, donnees, format: str = "json") -> str:
    """
    `donnees` : n'importe quelle structure sérialisable (dict/list
    imbriqués). `format` : "json" ou "xml".

    Uploade le fichier généré dans Supabase Storage, renvoie l'URL
    publique. Lève une exception si le format est invalide ou si
    l'upload échoue -- même contrat d'erreur que les autres modules
    generation_*.py.
    """
    format = format.lower().strip()
    if format not in ("json", "xml"):
        raise ValueError(f"Format non supporté : {format!r} (attendu : 'json' ou 'xml')")

    if format == "json":
        contenu = json.dumps(donnees, ensure_ascii=False, indent=2).encode("utf-8")
        extension, content_type = "json", "application/json"
    else:
        arbre = _dict_vers_xml(nom or "donnees", donnees)
        xml_brut = tostring(arbre, encoding="unicode")
        contenu = parseString(xml_brut).toprettyxml(indent="  ").encode("utf-8")
        extension, content_type = "xml", "application/xml"

    chemin = f"donnees/{uuid.uuid4()}-{nom}.{extension}"
    try:
        supabase.storage.from_(BUCKET).upload(chemin, contenu, {"content-type": content_type})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (upload donnees {chemin}) : {e}")
        raise

    return supabase.storage.from_(BUCKET).get_public_url(chemin)
