"""
Logique pure de création d'agent : génération d'id, extraction d'id
Notion, composition du system prompt. Zéro dépendance à Streamlit —
importable aussi bien depuis faces/vues/creer_agent.py (formulaire
Streamlit) que depuis api/agents.py (endpoint FastAPI), pour que les
deux ne dupliquent jamais cette logique (voir api/PLAN.md, décision #3).

Extrait de faces/vues/creer_agent.py au moment de construire l'API
(Étape 1 du plan), sans changement de comportement.
"""

import re


def generer_id_depuis_nom(nom):
    """
    Transforme "Coach fitness" en "coach-fitness". Doit rester unique dans
    la table `agents` (clé primaire texte) — la vérification d'unicité se
    fait à l'appel (Supabase), pas ici.
    """
    slug = nom.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def extraire_id_notion(lien_ou_id):
    """
    Accepte soit un lien Notion complet (https://www.notion.so/Titre-xxxxx),
    soit l'ID brut (avec ou sans tirets), et retourne toujours l'ID au
    format UUID standard attendu par indexers/index_notion.py (déjà
    multi-agent, lit agents.notion_page_id pour chaque agent).
    Retourne None si rien d'exploitable n'est trouvé (champ optionnel).
    """
    if not lien_ou_id or not lien_ou_id.strip():
        return None
    hex_seul = re.sub(r"[^a-f0-9]", "", lien_ou_id.strip().lower())
    if len(hex_seul) < 32:
        return None
    brut = hex_seul[-32:]
    return f"{brut[0:8]}-{brut[8:12]}-{brut[12:16]}-{brut[16:20]}-{brut[20:32]}"


def composer_system_prompt(
    ton, posture_generale, limites_globales, lignes_comportement,
    type_connaissance, description_connaissance,
):
    """
    Assemble les champs structurés des points 1, 2 et 4 du cadre de
    conception en UN SEUL texte brut (jamais de JSON) : c'est ce texte,
    et rien d'autre, qui est envoyé au LLM comme system prompt (voir
    core/configuration.py, colonne agents.system_prompt).

    `lignes_comportement` : liste de tuples (type_requete, comportement),
    les lignes vides (l'un des deux champs vide) sont ignorées.

    Reste modifiable tel quel ensuite par le créateur depuis "Mes
    agents" — composition automatique à la création, texte libre
    éditable après coup.
    """
    parties = []

    bloc_identite = [f"Ton : {ton}."]
    if posture_generale.strip():
        bloc_identite.append(f"Posture générale : {posture_generale.strip()}.")
    if limites_globales.strip():
        bloc_identite.append(
            f"Limites globales, à ne jamais franchir : {limites_globales.strip()}"
        )
    parties.append("## Identité\n" + "\n".join(bloc_identite))

    lignes_utiles = [
        (t.strip(), c.strip()) for t, c in lignes_comportement if t.strip() and c.strip()
    ]
    if lignes_utiles:
        bloc_comportement = "\n".join(f"- {t} : {c}" for t, c in lignes_utiles)
        parties.append("## Comportement selon le type de requête\n" + bloc_comportement)

    bloc_connaissance = [f"Nature de la connaissance : {type_connaissance}."]
    if description_connaissance.strip():
        bloc_connaissance.append(f"Contenu : {description_connaissance.strip()}")
    parties.append("## Base de connaissance\n" + "\n".join(bloc_connaissance))

    return "\n\n".join(parties)
