"""
Étape 1 du plan (voir api/PLAN.md) : POST /api/agents.

Équivalent du formulaire faces/vues/creer_agent.py, SANS l'upload de PDF
(volontairement laissé à l'Étape 2 : POST /api/agents/{id}/documents —
un fichier ne se transporte pas naturellement dans le même corps JSON
qu'un formulaire structuré, et creer_agent.py traite déjà ces deux
aspects comme deux blocs largement indépendants).

Réutilise telle quelle la logique déjà partagée avec le formulaire
Streamlit (core/creation_agent.py), pas de duplication (voir décision
d'architecture #3 dans api/PLAN.md).
"""

import os
import sys
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, supabase, get_secret

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "indexers"))
from creation_agent import generer_id_depuis_nom, extraire_id_notion, composer_system_prompt  # noqa: E402
from index_documents import indexer_texte  # noqa: E402

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class LigneComportement(BaseModel):
    type_requete: str = ""
    comportement: str = ""


class UiConfig(BaseModel):
    """Miroir des champs 'Interface'/'Thème visuel' de creer_agent.py."""
    icone_page: str = "🤖"
    sous_titre_accueil: str = ""
    placeholder_saisie: str = "Pose ta question..."
    style_titre: str = "unique"  # "unique" | "multicolore"
    titre_couleur_unique: str = "#000000"
    titre_couleurs_lettres: Optional[List[str]] = None
    bulle_assistant_visible: bool = True
    raisonnement_visible: bool = False
    rendu_visuel: bool = False
    memoire_visible: bool = True
    couleur_fond_page: str = "#FFFFFF"
    couleur_fond: str = "#646464"
    couleur_bulle_assistant: str = "#FFFFFF"
    couleur_texte_utilisateur: str = "#FFFFFF"
    couleur_texte_assistant: str = "#000000"
    couleur_texte_bouton: str = "#FFFFFF"
    couleur_accent: str = "#8B5E3C"
    couleur_lien: str = ""
    couleur_bouton_fond: str = ""
    couleur_bordure: str = "#808080"
    rayon_bulles: str = "Moyen"
    taille_texte: str = "Moyen"
    police: str = "Police système"
    css_avance: str = ""


class CreerAgentPayload(BaseModel):
    nom: str
    ton: str  # "Tutoiement (tu)" | "Vouvoiement (vous)"
    posture_generale: str = ""
    limites_globales: str = ""
    comportements: List[LigneComportement] = Field(default_factory=list)
    outils_choisis: List[str] = Field(default_factory=list)
    type_connaissance: str
    description_connaissance: str = ""
    lien_notion: Optional[str] = None
    texte_libre: str = ""
    ui_config: UiConfig = Field(default_factory=UiConfig)


class AgentCree(BaseModel):
    id: str
    nom: str
    lien: Optional[str] = None


@router.post("", response_model=AgentCree, status_code=201)
def creer_agent(payload: CreerAgentPayload, utilisateur=Depends(utilisateur_courant)):
    if not payload.nom.strip():
        raise HTTPException(status_code=422, detail="Le nom de l'agent est obligatoire.")
    if not payload.posture_generale.strip() and not payload.limites_globales.strip():
        raise HTTPException(
            status_code=422,
            detail="Remplis au moins la posture générale ou les limites globales.",
        )

    agent_id = generer_id_depuis_nom(payload.nom)

    try:
        existe_deja = (
            supabase.table("agents").select("id").eq("id", agent_id).maybe_single().execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification unicité agent_id={agent_id}) : {e}")
        existe_deja = None

    if existe_deja and existe_deja.data:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Un agent existe déjà avec un nom trop proche (id généré: {agent_id}). "
                "Choisis un nom légèrement différent."
            ),
        )

    lignes_comportement = [(l.type_requete, l.comportement) for l in payload.comportements]
    system_prompt = composer_system_prompt(
        payload.ton, payload.posture_generale, payload.limites_globales,
        lignes_comportement, payload.type_connaissance, payload.description_connaissance,
    )
    notion_page_id = extraire_id_notion(payload.lien_notion)

    ui = payload.ui_config
    ui_config_dict = {
        "titre_page": payload.nom.strip(),
        "icone_page": ui.icone_page.strip() or "🤖",
        "titre_accueil": f"{ui.icone_page.strip()} {payload.nom.strip()}",
        "sous_titre_accueil": ui.sous_titre_accueil.strip(),
        "titre_couleur_unique": ui.titre_couleur_unique if ui.style_titre == "unique" else "#000000",
        "titre_couleurs_lettres": ui.titre_couleurs_lettres if ui.style_titre == "multicolore" else None,
        "emoji_reponse": ui.icone_page.strip(),
        "placeholder_saisie": ui.placeholder_saisie.strip() or "Pose ta question...",
        "raisonnement_visible": ui.raisonnement_visible,
        "rendu_visuel": ui.rendu_visuel,
        "memoire_visible": ui.memoire_visible,
        "couleur_fond": ui.couleur_fond,
        "couleur_accent": ui.couleur_accent,
        "couleur_bulle_assistant": ui.couleur_bulle_assistant if ui.bulle_assistant_visible else "transparent",
        "bulle_assistant_visible": ui.bulle_assistant_visible,
        "couleur_bordure": ui.couleur_bordure,
        "couleur_fond_page": ui.couleur_fond_page,
        "couleur_texte_utilisateur": ui.couleur_texte_utilisateur,
        "couleur_texte_assistant": ui.couleur_texte_assistant,
        "couleur_texte_bouton": ui.couleur_texte_bouton,
        "couleur_lien": ui.couleur_lien,
        "couleur_bouton_fond": ui.couleur_bouton_fond,
        "rayon_bulles": ui.rayon_bulles,
        "taille_texte": ui.taille_texte,
        "police": ui.police,
        "css_avance": ui.css_avance.strip(),
    }

    knowledge_source = {
        "type": payload.type_connaissance,
        "description": payload.description_connaissance.strip(),
        # Conservé tel quel (pas seulement indexé), même choix que
        # creer_agent.py, pour pouvoir être réaffiché/modifié plus tard.
        "texte_libre": payload.texte_libre.strip(),
    }

    nouvelle_ligne = {
        "id": agent_id,
        "nom": payload.nom.strip(),
        "system_prompt": system_prompt,
        "ui_config": ui_config_dict,
        "knowledge_source": knowledge_source,
        "tools_enabled": payload.outils_choisis,
        "owner_id": utilisateur.id,
    }
    if notion_page_id:
        nouvelle_ligne["notion_page_id"] = notion_page_id

    try:
        supabase.table("agents").insert(nouvelle_ligne).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion agent {agent_id}) : {e}")
        raise HTTPException(
            status_code=500,
            detail="Impossible de créer l'agent (erreur technique). Réessaie dans un instant.",
        )

    # Indexation du texte libre : best-effort, n'annule jamais la création
    # de l'agent (même choix que creer_agent.py) si elle échoue.
    if payload.texte_libre.strip():
        try:
            indexer_texte(agent_id, "texte-libre", payload.texte_libre.strip())
        except Exception as e:
            logging.error(f"ERREUR indexation texte libre (agent_id={agent_id}) : {e}")

    url_base = get_secret("URL_RETOUR_APP")
    lien = f"{url_base.rstrip('/')}/?agent={agent_id}" if url_base else None
    if not url_base:
        logging.error("URL_RETOUR_APP absent : impossible de construire le lien complet de l'agent.")

    return AgentCree(id=agent_id, nom=payload.nom.strip(), lien=lien)
