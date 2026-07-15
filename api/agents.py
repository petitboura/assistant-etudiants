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
import tempfile
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from api.auth import utilisateur_courant, supabase, get_secret

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "core"))
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "indexers"))
from creation_agent import generer_id_depuis_nom, extraire_id_notion, composer_system_prompt  # noqa: E402
from index_documents import indexer_texte, indexer_document, supprimer_chunks_existants  # noqa: E402
from storage import upload_document, list_documents, delete_document, get_document_url  # noqa: E402

logging.basicConfig(level=logging.INFO)

router = APIRouter(prefix="/api/agents", tags=["agents"])


class LigneComportement(BaseModel):
    type_requete: str = ""
    comportement: str = ""


class UiConfig(BaseModel):
    """
    Depuis le pivot social (2026-07-11, voir PIVOT_SOCIAL.md, section
    "Ce qui change") : le thème visuel par agent est supprimé, un seul
    thème fixe s'applique à toute la plateforme. Seul icone_page reste
    personnalisable ici — tous les anciens champs (couleurs, police,
    rayon des bulles, CSS avancé, style de titre multicolore...) sont
    retirés, pas juste ignorés, pour ne pas garder de code mort côté API.
    Cible finale de `agents.ui_config` en base (le nettoyage de la
    colonne elle-même, avec les agents déjà créés, reste une étape à
    part, voir PIVOT_SOCIAL.md Étape B).
    """
    icone_page: str = "🤖"
    # Ajouté le 2026-07-14 (Bourama : le formulaire n'avait aucune section
    # pour ce champ, alors que faces/vues/chat.py le lit déjà depuis
    # ui_config.placeholder_saisie -- voir UI_CONFIG_PAR_DEFAUT). Le
    # formulaire Streamlit (creer_agent.py) l'avait déjà, seul le flow
    # Next.js en manquait.
    placeholder_saisie: str = "Pose ta question..."


class CreerAgentPayload(BaseModel):
    nom: str
    ton: str  # "Tutoiement (tu)" | "Vouvoiement (vous)"
    posture_generale: str = ""
    limites_globales: str = ""
    comportements: List[LigneComportement] = Field(default_factory=list)
    outils_choisis: List[str] = Field(default_factory=list)
    # Optionnel depuis le 2026-07-12 (Bourama : champ "Nature de la
    # connaissance" retiré du formulaire Next.js -- voir docstring de
    # composer_system_prompt). Le formulaire Streamlit continue d'envoyer
    # une valeur, donc reste géré normalement quand fourni.
    type_connaissance: str = ""
    description_connaissance: str = ""
    lien_notion: Optional[str] = None
    texte_libre: str = ""
    ui_config: UiConfig = Field(default_factory=UiConfig)
    # Ajouté le 2026-07-15 (Bourama : système de catégories) : obligatoire
    # à la création (voir validation plus bas), doit référencer une ligne
    # existante de la table `categories`.
    categorie_id: str
    # Nouveau flow de création (pivot social) : image de vitrine et
    # description publique de la page agent, distinctes de
    # description_connaissance qui reste un usage interne au RAG.
    image_vitrine_url: Optional[str] = None
    description: str = ""
    # Ajouté le 2026-07-12 (Bourama : "tu as mélangé deux choses, la
    # description publique et le sous-titre. La description publique peut
    # avoir n'importe quelle taille alors que le sous-titre n'est qu'un
    # sous-titre"). AVANT ce correctif, sous_titre_accueil était rempli
    # directement avec `description` (voir plus bas) faute de champ dédié
    # -- ça marchait "par accident" pour corriger le bug du sous-titre
    # identique à tous les agents, mais confondait deux choses de nature
    # différente : `description` = texte public de longueur libre (fiche
    # agent, SEO), `sous_titre` = courte phrase d'accueil affichée sous le
    # titre au premier écran du chat (équivalent du champ "Phrase
    # d'accueil" du formulaire Streamlit, faces/vues/creer_agent.py).
    # Fallback sur `description` uniquement si `sous_titre` est vide, pour
    # ne pas laisser un agent sans aucun sous-titre si le créateur ne
    # remplit pas ce nouveau champ.
    sous_titre: str = ""


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
    if not payload.categorie_id.strip():
        raise HTTPException(status_code=422, detail="La catégorie est obligatoire.")

    try:
        categorie_existe = (
            supabase.table("categories")
            .select("id")
            .eq("id", payload.categorie_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (vérification catégorie={payload.categorie_id}) : {e}")
        categorie_existe = None
    if not categorie_existe or not categorie_existe.data:
        raise HTTPException(status_code=422, detail="Catégorie inconnue.")

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
        nom=payload.nom, description_publique=payload.description,
    )
    notion_page_id = extraire_id_notion(payload.lien_notion)

    # Depuis le pivot social : plus de personnalisation de thème par agent,
    # seuls titre/icône/emoji dérivés du nom et de l'icône restent écrits
    # dans ui_config. faces/vues/chat.py retombe sur UI_CONFIG_PAR_DEFAUT
    # pour tout le reste (couleurs, police, rendu_visuel, etc.), ce qui
    # est le comportement voulu : un seul thème fixe pour la plateforme.
    ui = payload.ui_config
    ui_config_dict = {
        "titre_page": payload.nom.strip(),
        "icone_page": ui.icone_page.strip() or "🤖",
        "titre_accueil": f"{ui.icone_page.strip()} {payload.nom.strip()}",
        # Bug corrigé le 2026-07-12 (Bourama : "le sous-titre est
        # identique à tous, vraiment tous") : ce champ n'était jamais
        # écrit ici, donc faces/vues/chat.py retombait systématiquement
        # sur UI_CONFIG_PAR_DEFAUT["sous_titre_accueil"] (le texte de
        # l'agent maths historique) pour TOUS les agents créés via ce
        # flow, quel que soit leur sujet réel. Le formulaire Streamlit
        # (creer_agent.py) a un champ dédié pour ça ("Phrase d'accueil") ;
        # ce flow-ci en a maintenant un aussi (`sous_titre`), distinct de
        # `description` (correctif du 2026-07-12 suivant : les deux
        # avaient été confondus dans une première version de ce
        # correctif). Fallback sur description seulement si sous_titre
        # est vide, pour ne jamais laisser un agent sans sous-titre.
        "sous_titre_accueil": payload.sous_titre.strip() or payload.description.strip(),
        "emoji_reponse": ui.icone_page.strip(),
        # Point 5 (2026-07-14, Bourama) : texte de la barre de saisie du
        # chat, personnalisable par agent -- déjà lu tel quel côté
        # faces/vues/chat.py (UI_CONFIG["placeholder_saisie"]), seul le
        # flow de création Next.js ne l'écrivait pas encore.
        "placeholder_saisie": ui.placeholder_saisie.strip() or "Pose ta question...",
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
        # Colonnes ajoutées par la migration pivot_social_etape_b_tables
        # (voir PIVOT_SOCIAL.md, Étape B) : vitrine publique de l'agent,
        # distincte de knowledge_source.description (usage RAG interne).
        "image_vitrine_url": payload.image_vitrine_url,
        "description": payload.description.strip(),
        "categorie_id": payload.categorie_id,
        # Colonne ajoutée le 2026-07-12 (Bourama : le formulaire de
        # modification doit contenir tous les champs de la création).
        # composer_system_prompt() fusionne ces champs puis les jette —
        # sans cette colonne, impossible de les réafficher pour édition,
        # seul le texte composé final survivrait. Un agent créé AVANT
        # cette migration aura `config_creation IS NULL` (voir
        # obtenir_agent_pour_edition, qui gère ce cas en repli sur le
        # system_prompt brut). Inclut `sous_titre` (voir CreerAgentPayload
        # plus haut, distinct de `description`).
        "config_creation": {
            "ton": payload.ton,
            "posture_generale": payload.posture_generale,
            "limites_globales": payload.limites_globales,
            "comportements": [
                {"type_requete": l.type_requete, "comportement": l.comportement}
                for l in payload.comportements
            ],
            "type_connaissance": payload.type_connaissance,
            "description_connaissance": payload.description_connaissance,
            "sous_titre": payload.sous_titre,
        },
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


class AgentDetailPublic(BaseModel):
    id: str
    nom: str
    icone_page: str = "🤖"
    image_vitrine_url: Optional[str] = None
    description: str = ""
    owner_id: str


@router.get("/{agent_id}", response_model=AgentDetailPublic)
def obtenir_agent_public(agent_id: str):
    """
    Détail public d'un agent, pour la page `/agent/[slug]` (voir
    PIVOT_SOCIAL.md, Étape C, Étape E). Public, aucune auth requise, comme
    `/api/feed`. `agent_id` sert de slug : pas de colonne `slug` dédiée
    sur `agents` (voir PIVOT_SOCIAL.md, changelog "Étape B terminée").

    `owner_id` est renvoyé pour permettre au frontend de lier vers le
    portfolio créateur (`/u/[slug]`, Étape E) une fois `GET
    /api/profiles/{slug}` construit ; pas encore de résolution
    profil <-> agent ici, volontairement, pour ne pas dupliquer une
    logique qui appartient à l'endpoint profils.

    404 si l'agent n'existe pas OU s'il est désactivé (`actif` is
    False) : une page publique ne doit pas exister pour un agent
    désactivé, même en connaissant son id directement. Convention "True
    par défaut" si `actif` est absent/NULL, identique à
    `faces/vues/chat.py:_agent_est_actif` et à `/api/feed`.
    """
    try:
        res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description, owner_id, actif")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent public {agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger cet agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne.get("actif") is False:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    return AgentDetailPublic(
        id=ligne["id"],
        nom=ligne["nom"],
        icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
        image_vitrine_url=ligne.get("image_vitrine_url"),
        description=ligne.get("description") or "",
        owner_id=ligne["owner_id"],
    )


class MettreAJourVitrinePayload(BaseModel):
    # Optional (pas absent = pas de valeur) volontairement, pour un PATCH
    # partiel : un champ omis (None) n'est pas touché, contrairement à une
    # chaîne vide envoyée explicitement, qui efface la valeur existante.
    image_vitrine_url: Optional[str] = None
    description: Optional[str] = None


@router.patch("/{agent_id}/vitrine", response_model=AgentDetailPublic)
def mettre_a_jour_vitrine(
    agent_id: str,
    payload: MettreAJourVitrinePayload,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Mise à jour de la vitrine publique d'un agent (image + description),
    depuis le dashboard "Mes agents" (voir PIVOT_SOCIAL.md, Étape F).

    Vérifie que `owner_id` du token correspond au propriétaire de l'agent
    (403 sinon) — même exigence que celle notée pour l'upload de
    documents à l'Étape 2 de `api/PLAN.md`, appliquée ici en premier
    puisque c'est le premier endpoint de modification (hors création) du
    pivot social.
    """
    try:
        res = (
            supabase.table("agents")
            .select("id, nom, ui_config, image_vitrine_url, description, owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant mise à jour vitrine) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de mettre à jour la vitrine pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    mise_a_jour = {}
    if payload.image_vitrine_url is not None:
        mise_a_jour["image_vitrine_url"] = payload.image_vitrine_url
    if payload.description is not None:
        mise_a_jour["description"] = payload.description.strip()

    if not mise_a_jour:
        raise HTTPException(
            status_code=422,
            detail="Rien à mettre à jour (image_vitrine_url et description sont absents).",
        )

    try:
        supabase.table("agents").update(mise_a_jour).eq("id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (mise à jour vitrine agent {agent_id}) : {e}")
        raise HTTPException(
            status_code=500,
            detail="Impossible de mettre à jour la vitrine (erreur technique). Réessaie dans un instant.",
        )

    ligne.update(mise_a_jour)
    return AgentDetailPublic(
        id=ligne["id"],
        nom=ligne["nom"],
        icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
        image_vitrine_url=ligne.get("image_vitrine_url"),
        description=ligne.get("description") or "",
        owner_id=ligne["owner_id"],
    )


class ConfigCreation(BaseModel):
    """
    Les champs discrets du formulaire de création (ton, posture,
    limites, comportements, type de connaissance, sous-titre), stockés à
    part depuis le 2026-07-12 dans `agents.config_creation` (voir
    migration `agents_config_creation`) — avant cette date,
    `composer_system_prompt` les fusionnait dans `system_prompt` puis les
    jetait, aucune trace séparée ne survivait. Un agent créé avant cette
    migration a `config_creation IS NULL` : voir
    `obtenir_agent_pour_edition`, qui retombe sur le `system_prompt` brut
    dans ce cas plutôt que d'inventer des valeurs vides qui écraseraient
    un prompt déjà soigné à l'enregistrement suivant.
    """

    ton: str = "Tutoiement (tu)"
    posture_generale: str = ""
    limites_globales: str = ""
    comportements: List[LigneComportement] = Field(default_factory=list)
    type_connaissance: str = ""
    description_connaissance: str = ""
    sous_titre: str = ""


class AgentEditable(BaseModel):
    """
    Vue complète d'un agent pour SON propriétaire (contrairement à
    AgentDetailPublic, qui est ce que voit un visiteur).

    `config_creation` est `None` pour un agent créé avant le 2026-07-12
    (voir ConfigCreation) : dans ce cas, le frontend doit retomber sur
    l'édition du `system_prompt` brut (toujours renvoyé, à jour) plutôt
    que d'afficher un formulaire structuré avec des champs vides qui
    écraseraient le prompt existant s'ils étaient enregistrés tels quels.
    """

    id: str
    nom: str
    icone_page: str = "🤖"
    system_prompt: str = ""
    config_creation: Optional[ConfigCreation] = None
    tools_enabled: List[str] = Field(default_factory=list)
    notion_page_id: Optional[str] = None
    texte_libre: str = ""
    image_vitrine_url: Optional[str] = None
    description: str = ""
    sous_titre: str = ""
    placeholder_saisie: str = "Pose ta question..."
    actif: bool = True
    categorie_id: Optional[str] = None


@router.get("/{agent_id}/edition", response_model=AgentEditable)
def obtenir_agent_pour_edition(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Ajouté le 2026-07-12 (Bourama : "on ne peut pas modifier ces agents
    créés", gros morceau manquant depuis le début du pivot social — la
    seule modification possible jusqu'ici était `mettre_a_jour_vitrine`,
    jamais branchée à aucune page côté Next.js). Réservé au propriétaire
    (403 sinon) : contrairement à `obtenir_agent_public`, cette vue
    contient le `system_prompt` complet, pas destiné aux visiteurs.
    """
    try:
        res = (
            supabase.table("agents")
            .select(
                "id, nom, ui_config, system_prompt, config_creation, tools_enabled, "
                "notion_page_id, knowledge_source, image_vitrine_url, description, "
                "actif, owner_id, categorie_id"
            )
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} pour édition) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger l'agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    config_brut = ligne.get("config_creation")

    return AgentEditable(
        id=ligne["id"],
        nom=ligne["nom"],
        icone_page=(ligne.get("ui_config") or {}).get("icone_page", "🤖"),
        system_prompt=ligne.get("system_prompt") or "",
        config_creation=ConfigCreation(**config_brut) if config_brut else None,
        tools_enabled=ligne.get("tools_enabled") or [],
        notion_page_id=ligne.get("notion_page_id"),
        texte_libre=(ligne.get("knowledge_source") or {}).get("texte_libre", ""),
        image_vitrine_url=ligne.get("image_vitrine_url"),
        description=ligne.get("description") or "",
        sous_titre=(ligne.get("ui_config") or {}).get("sous_titre_accueil", ""),
        placeholder_saisie=(ligne.get("ui_config") or {}).get(
            "placeholder_saisie", "Pose ta question..."
        ),
        actif=ligne.get("actif", True),
        categorie_id=ligne.get("categorie_id"),
    )


class ModifierAgentPayload(BaseModel):
    # Tous optionnels : PATCH partiel, un champ omis (None) n'est pas
    # touché — même convention que MettreAJourVitrinePayload.
    nom: Optional[str] = None
    icone_page: Optional[str] = None
    # Champs discrets (formulaire structuré, voir ConfigCreation) : si
    # AU MOINS UN est fourni, le system_prompt est RECOMPOSÉ à partir
    # d'eux (fusionnés avec config_creation existant pour les champs
    # omis) et system_prompt ci-dessous est alors ignoré. À utiliser
    # quand AgentEditable.config_creation n'était pas None.
    ton: Optional[str] = None
    posture_generale: Optional[str] = None
    limites_globales: Optional[str] = None
    comportements: Optional[List[LigneComportement]] = None
    type_connaissance: Optional[str] = None
    description_connaissance: Optional[str] = None
    sous_titre: Optional[str] = None
    # Point 5 (2026-07-14, Bourama) : indépendant de nom/icone_page/
    # sous_titre, même traitement que sous_titre dans le handler plus bas
    # (sa propre condition, pas rattaché au bloc nom/icone).
    placeholder_saisie: Optional[str] = None
    # Repli brut (agents pré-migration, AgentEditable.config_creation
    # était None) : ignoré si un champ discret ci-dessus est fourni.
    system_prompt: Optional[str] = None
    lien_notion: Optional[str] = None
    texte_libre: Optional[str] = None
    image_vitrine_url: Optional[str] = None
    description: Optional[str] = None
    actif: Optional[bool] = None
    categorie_id: Optional[str] = None


@router.patch("/{agent_id}", response_model=AgentEditable)
def modifier_agent(
    agent_id: str,
    payload: ModifierAgentPayload,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Ajouté le 2026-07-12, voir AgentEditable/obtenir_agent_pour_edition
    ci-dessus pour le contexte. `agents.id` n'est JAMAIS modifié ici même
    si `nom` change : l'id sert de slug dans les URLs publiques
    (/agent/{id}) et de clé étrangère pour `agent_ratings`,
    `agent_comments`, `follows` — le renommer casserait tous les liens
    déjà partagés et les FK existantes. Seul `nom` (colonne d'affichage)
    et les champs dérivés dans `ui_config` (titre_page, titre_accueil,
    emoji_reponse, sous_titre_accueil) changent.
    """
    try:
        res = (
            supabase.table("agents")
            .select(
                "id, nom, ui_config, system_prompt, config_creation, tools_enabled, "
                "notion_page_id, knowledge_source, image_vitrine_url, description, "
                "actif, owner_id, categorie_id"
            )
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant modification) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de modifier l'agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")

    ligne = res.data
    if ligne["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    mise_a_jour = {}

    nom_final = ligne["nom"]
    if payload.nom is not None and payload.nom.strip():
        nom_final = payload.nom.strip()
        mise_a_jour["nom"] = nom_final

    ui_config = dict(ligne.get("ui_config") or {})
    icone_finale = ui_config.get("icone_page", "🤖")
    if payload.icone_page is not None and payload.icone_page.strip():
        icone_finale = payload.icone_page.strip()

    ui_config_modifie = False

    if payload.nom is not None or payload.icone_page is not None:
        ui_config.update(
            {
                "titre_page": nom_final,
                "icone_page": icone_finale,
                "titre_accueil": f"{icone_finale} {nom_final}",
                "emoji_reponse": icone_finale,
            }
        )
        ui_config_modifie = True

    # sous_titre_accueil est indépendant de nom/icône, donc géré dans sa
    # propre condition plutôt que rattaché au bloc nom/icone_page
    # ci-dessus (voir la fix d'origine de ce champ) -- sinon modifier
    # UNIQUEMENT le sous-titre (sans toucher nom ni icône) n'aurait
    # jamais été écrit en base. Peut aussi arriver via les champs
    # discrets (recomposition ci-dessous) : le bloc discret prend le
    # dessus s'il est présent, voir plus bas.
    if payload.sous_titre is not None:
        ui_config["sous_titre_accueil"] = payload.sous_titre.strip()
        ui_config_modifie = True

    # Même raisonnement que sous_titre_accueil juste au-dessus (2026-07-14,
    # Bourama) : indépendant de nom/icône, sa propre condition.
    if payload.placeholder_saisie is not None:
        ui_config["placeholder_saisie"] = (
            payload.placeholder_saisie.strip() or "Pose ta question..."
        )
        ui_config_modifie = True

    if ui_config_modifie:
        mise_a_jour["ui_config"] = ui_config

    # Recomposition depuis les champs discrets si au moins un est fourni
    # (formulaire structuré, agent avec config_creation existant) ;
    # sinon repli sur system_prompt brut si fourni (agent pré-migration).
    config_actuel = dict(ligne.get("config_creation") or {})
    champs_discrets_fournis = any(
        v is not None
        for v in (
            payload.ton,
            payload.posture_generale,
            payload.limites_globales,
            payload.comportements,
            payload.type_connaissance,
            payload.description_connaissance,
        )
    )
    if champs_discrets_fournis:
        nouveau_config = {
            "ton": payload.ton if payload.ton is not None else config_actuel.get("ton", "Tutoiement (tu)"),
            "posture_generale": (
                payload.posture_generale
                if payload.posture_generale is not None
                else config_actuel.get("posture_generale", "")
            ),
            "limites_globales": (
                payload.limites_globales
                if payload.limites_globales is not None
                else config_actuel.get("limites_globales", "")
            ),
            "comportements": (
                [{"type_requete": l.type_requete, "comportement": l.comportement} for l in payload.comportements]
                if payload.comportements is not None
                else config_actuel.get("comportements", [])
            ),
            "type_connaissance": (
                payload.type_connaissance
                if payload.type_connaissance is not None
                else config_actuel.get("type_connaissance", "")
            ),
            "description_connaissance": (
                payload.description_connaissance
                if payload.description_connaissance is not None
                else config_actuel.get("description_connaissance", "")
            ),
            "sous_titre": (
                payload.sous_titre if payload.sous_titre is not None else config_actuel.get("sous_titre", "")
            ),
        }
        lignes_comportement = [
            (c["type_requete"], c["comportement"]) for c in nouveau_config["comportements"]
        ]
        mise_a_jour["system_prompt"] = composer_system_prompt(
            nouveau_config["ton"],
            nouveau_config["posture_generale"],
            nouveau_config["limites_globales"],
            lignes_comportement,
            nouveau_config["type_connaissance"],
            nouveau_config["description_connaissance"],
            nom=nom_final,
            description_publique=(
                payload.description if payload.description is not None else ligne.get("description") or ""
            ),
        )
        mise_a_jour["config_creation"] = nouveau_config
        # Le sous-titre discret prend le dessus sur celui déjà posé par
        # payload.sous_titre ci-dessus (même valeur si les deux sont
        # fournis, ce bloc est juste la source de vérité en cas de
        # formulaire structuré).
        ui_config["sous_titre_accueil"] = nouveau_config["sous_titre"] or ui_config.get(
            "sous_titre_accueil", ""
        )
        mise_a_jour["ui_config"] = ui_config
    elif payload.system_prompt is not None:
        mise_a_jour["system_prompt"] = payload.system_prompt.strip()

    if payload.lien_notion is not None:
        mise_a_jour["notion_page_id"] = extraire_id_notion(payload.lien_notion)

    knowledge_source = dict(ligne.get("knowledge_source") or {})
    if payload.texte_libre is not None:
        knowledge_source["texte_libre"] = payload.texte_libre.strip()
        mise_a_jour["knowledge_source"] = knowledge_source

    if payload.image_vitrine_url is not None:
        mise_a_jour["image_vitrine_url"] = payload.image_vitrine_url
    if payload.description is not None:
        mise_a_jour["description"] = payload.description.strip()
    if payload.actif is not None:
        mise_a_jour["actif"] = payload.actif
    if payload.categorie_id is not None:
        try:
            categorie_existe = (
                supabase.table("categories")
                .select("id")
                .eq("id", payload.categorie_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (vérification catégorie={payload.categorie_id}) : {e}")
            categorie_existe = None
        if not categorie_existe or not categorie_existe.data:
            raise HTTPException(status_code=422, detail="Catégorie inconnue.")
        mise_a_jour["categorie_id"] = payload.categorie_id

    if not mise_a_jour:
        raise HTTPException(status_code=422, detail="Rien à modifier.")

    try:
        # .eq("owner_id", ...) en plus de .eq("id", ...) : sécurité
        # redondante avec le check ci-dessus, même précaution que
        # faces/vues/mes_agents.py (qui scope aussi son .update() par
        # owner_id, pas seulement par un if avant).
        supabase.table("agents").update(mise_a_jour).eq("id", agent_id).eq(
            "owner_id", utilisateur.id
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (modification agent {agent_id}) : {e}")
        raise HTTPException(
            status_code=500,
            detail="Impossible de modifier l'agent (erreur technique). Réessaie dans un instant.",
        )

    # Réindexation du texte libre : best-effort, même choix que la
    # création (indexer_texte remplace toujours les anciens chunks pour
    # ce nom_fichier, voir supprimer_chunks_existants — pas de doublons
    # même si ce formulaire est réenregistré plusieurs fois).
    if payload.texte_libre is not None and payload.texte_libre.strip():
        try:
            indexer_texte(agent_id, "texte-libre", payload.texte_libre.strip())
        except Exception as e:
            logging.error(f"ERREUR réindexation texte libre (agent_id={agent_id}) : {e}")

    config_final = mise_a_jour.get("config_creation", ligne.get("config_creation"))

    return AgentEditable(
        id=agent_id,
        nom=nom_final,
        icone_page=icone_finale,
        system_prompt=mise_a_jour.get("system_prompt", ligne.get("system_prompt") or ""),
        config_creation=ConfigCreation(**config_final) if config_final else None,
        tools_enabled=ligne.get("tools_enabled") or [],
        notion_page_id=mise_a_jour.get("notion_page_id", ligne.get("notion_page_id")),
        texte_libre=knowledge_source.get("texte_libre", ""),
        image_vitrine_url=mise_a_jour.get("image_vitrine_url", ligne.get("image_vitrine_url")),
        description=mise_a_jour.get("description", ligne.get("description") or ""),
        sous_titre=ui_config.get("sous_titre_accueil", ""),
        actif=mise_a_jour.get("actif", ligne.get("actif", True)),
        categorie_id=mise_a_jour.get("categorie_id", ligne.get("categorie_id")),
    )


@router.post("/{agent_id}/documents", status_code=201)
async def uploader_document(
    agent_id: str,
    fichier: UploadFile = File(...),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Étape 2 de `api/PLAN.md`, jamais construite jusqu'ici — ajoutée le
    2026-07-12 suite à un bug remonté par Bourama : le nouveau formulaire
    de création (D.6 du pivot social) n'avait aucun moyen d'ajouter un
    PDF, `POST /api/agents` ne le gère pas lui-même (voir docstring en
    tête de ce fichier). Appelé APRÈS `POST /api/agents` : l'agent doit
    déjà exister, on a besoin de son id pour indexer le document dessus.

    Réutilise telle quelle la logique déjà en place côté Streamlit
    (`indexers/storage.py:upload_document` +
    `indexers/index_documents.py:indexer_document`) — pas de duplication,
    même convention que `composer_system_prompt` (décision d'architecture
    #3 de `api/PLAN.md`).

    Vérifie la propriété de l'agent (même exigence que
    `mettre_a_jour_vitrine`, notée dès l'Étape 1 comme prérequis pour ce
    endpoint).
    """
    if fichier.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Seuls les fichiers PDF sont acceptés.")

    try:
        res = (
            supabase.table("agents")
            .select("id, owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant upload document) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'ajouter ce document pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    contenu = await fichier.read()
    if len(contenu) == 0:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    nom_original = fichier.filename or "document.pdf"
    nom_stockage = f"{agent_id}__{nom_original}"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contenu)
        chemin_temp = tmp.name

    try:
        upload_document(chemin_temp, nom_stockage)
        indexer_document(chemin_temp, nom_stockage, agent_id)
    except Exception as e:
        logging.error(f"ERREUR indexation PDF (agent_id={agent_id}, fichier={nom_original}) : {e}")
        raise HTTPException(
            status_code=500,
            detail=f"L'agent est créé, mais « {nom_original} » n'a pas pu être indexé. Réessaie depuis « Mes agents ».",
        )
    finally:
        try:
            os.remove(chemin_temp)
        except OSError:
            pass

    return {"nom": nom_original, "statut": "indexé"}


@router.get("/{agent_id}/documents")
def lister_documents(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    Ajouté le 2026-07-12, même contexte que `modifier_agent` (édition
    complète d'un agent, demandée par Bourama). Réutilise
    `indexers/storage.py:list_documents` telle quelle (liste TOUT le
    bucket, pas de filtre côté Supabase Storage par préfixe) puis filtre
    en Python sur `{agent_id}__` — même approche que
    `faces/vues/mes_agents.py` fait déjà, pas une nouvelle logique.
    """
    try:
        res = (
            supabase.table("agents")
            .select("owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant liste documents) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de lister les documents pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    try:
        tous_les_fichiers = list_documents()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (liste documents, agent_id={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de lister les documents pour le moment.")

    prefixe = f"{agent_id}__"
    fichiers_agent = [f for f in tous_les_fichiers if f.startswith(prefixe)]

    return [
        {
            "nom_stockage": f,
            "nom_affiche": f[len(prefixe):],
            "url": get_document_url(f),
        }
        for f in fichiers_agent
    ]


@router.delete("/{agent_id}/documents/{nom_stockage}", status_code=204)
def supprimer_document(agent_id: str, nom_stockage: str, utilisateur=Depends(utilisateur_courant)):
    """
    Ajouté le 2026-07-12, même contexte. Vérifie que `nom_stockage`
    commence bien par `{agent_id}__` (pas juste que l'agent appartient à
    l'utilisateur) : sinon un propriétaire d'un agent A pourrait passer
    le nom de stockage d'un document de l'agent B et le supprimer, tant
    que A lui appartient. Supprime aussi les chunks vectorisés associés
    (`supprimer_chunks_existants`), sinon le RAG continuerait à retrouver
    le contenu d'un PDF qui n'existe plus dans le stockage — même
    précaution que `faces/vues/mes_agents.py`.
    """
    try:
        res = (
            supabase.table("agents")
            .select("owner_id")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant suppression document) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer ce document pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    if not nom_stockage.startswith(f"{agent_id}__"):
        raise HTTPException(status_code=403, detail="Ce document n'appartient pas à cet agent.")

    try:
        delete_document(nom_stockage)
        supprimer_chunks_existants(agent_id, nom_stockage)
    except Exception as e:
        logging.error(f"ERREUR suppression document {nom_stockage} (agent_id={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer ce document.")


class NoterAgentPayload(BaseModel):
    # Classe manquante : provoquait un NameError au chargement du module,
    # qui faisait crasher TOUT le service au démarrage (pas seulement cet
    # endpoint) - Railway l'a marqué "CRASHED" juste après le déploiement
    # de main.py du 2026-07-13. La fonction noter_agent juste en dessous
    # l'utilisait déjà, seule la définition manquait.
    note: int


@router.post("/{agent_id}/rating", status_code=204)
def noter_agent(agent_id: str, payload: NoterAgentPayload, utilisateur=Depends(utilisateur_courant)):
    """
    Note un agent de 1 à 5 (table `agent_ratings`, voir PIVOT_SOCIAL.md :
    contrainte unique `(agent_id, user_id)` — un utilisateur note un agent
    une seule fois mais peut modifier sa note). Upsert plutôt qu'insert
    pour porter ce comportement directement, sans 409 + endpoint PATCH
    séparé pour le même geste côté frontend (contrairement à `/vitrine`,
    qui est une vraie modification d'un objet déjà possédé).

    Ne vérifie pas que l'agent existe avant d'insérer : la contrainte FK
    `agent_id` fera déjà échouer l'upsert proprement si l'agent n'existe
    pas, pas besoin de dupliquer cette vérification ici.
    """
    if not 1 <= payload.note <= 5:
        raise HTTPException(status_code=422, detail="La note doit être comprise entre 1 et 5.")

    try:
        supabase.table("agent_ratings").upsert(
            {"agent_id": agent_id, "user_id": utilisateur.id, "note": payload.note},
            on_conflict="agent_id,user_id",
        ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (upsert note agent={agent_id}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer la note pour le moment.")


class NoteAgregee(BaseModel):
    moyenne: Optional[float] = None
    total: int = 0


@router.get("/{agent_id}/rating", response_model=NoteAgregee)
def obtenir_note_agent(agent_id: str):
    """
    Note moyenne publique d'un agent, pour l'affichage "note 1-5" sur
    `/agent/[slug]` (voir PIVOT_SOCIAL.md, tableau des pages du frontend).
    Public, aucune auth. `moyenne` reste `None` (pas 0) tant qu'aucune
    note n'existe, pour que le frontend distingue "pas encore noté" de
    "noté 0".
    """
    try:
        res = supabase.table("agent_ratings").select("note").eq("agent_id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture notes agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger la note pour le moment.")

    notes = [ligne["note"] for ligne in (res.data or [])]
    if not notes:
        return NoteAgregee(moyenne=None, total=0)
    return NoteAgregee(moyenne=round(sum(notes) / len(notes), 2), total=len(notes))


class CommentaireCree(BaseModel):
    contenu: str


class Commentaire(BaseModel):
    id: str
    agent_id: str
    user_id: str
    # Nom affiché du profil de l'auteur, résolu par jointure côté serveur
    # (voir lister_commentaires / creer_commentaire). None si l'auteur n'a
    # jamais renseigné de profil (PATCH /api/profiles/me jamais appelé) —
    # le frontend décide de l'affichage de repli dans ce cas, pas ici.
    nom_affiche: Optional[str] = None
    contenu: str
    created_at: Optional[str] = None


@router.post("/{agent_id}/comments", response_model=Commentaire, status_code=201)
def creer_commentaire(agent_id: str, payload: CommentaireCree, utilisateur=Depends(utilisateur_courant)):
    """
    Ajoute un commentaire sur un agent (table `agent_comments`, voir
    PIVOT_SOCIAL.md). Un commentaire par appel ; aucune limite de nombre
    par utilisateur pour l'instant, aucune modération demandée par
    Bourama à ce stade — à revoir si besoin plus tard.
    """
    contenu = payload.contenu.strip()
    if not contenu:
        raise HTTPException(status_code=422, detail="Le commentaire ne peut pas être vide.")

    try:
        res = (
            supabase.table("agent_comments")
            .insert({"agent_id": agent_id, "user_id": utilisateur.id, "contenu": contenu})
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (insertion commentaire agent={agent_id}, user={utilisateur.id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer le commentaire pour le moment.")

    if not res.data:
        raise HTTPException(status_code=500, detail="Le commentaire n'a pas pu être créé (erreur technique).")

    ligne = res.data[0]

    # Best-effort : le nom affiché n'est pas critique au point de faire
    # échouer la création du commentaire si cette lecture rate.
    nom_affiche = None
    try:
        profil = (
            supabase.table("profiles")
            .select("nom_affiche")
            .eq("user_id", utilisateur.id)
            .maybe_single()
            .execute()
        )
        if profil and profil.data:
            nom_affiche = profil.data.get("nom_affiche") or None
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture nom_affiche pour commentaire, user={utilisateur.id}) : {e}")

    return Commentaire(
        id=str(ligne["id"]),
        agent_id=ligne["agent_id"],
        user_id=ligne["user_id"],
        nom_affiche=nom_affiche,
        contenu=ligne["contenu"],
        created_at=ligne.get("created_at"),
    )


@router.get("/{agent_id}/comments", response_model=List[Commentaire])
def lister_commentaires(
    agent_id: str,
    page: int = Query(1, ge=1),
    limite: int = Query(20, ge=1, le=50),
):
    """
    Liste paginée des commentaires d'un agent, plus récents d'abord.
    Public, aucune auth requise. Mêmes bornes de pagination que
    `/api/feed` (limite plafonnée à 50/page).
    """
    debut = (page - 1) * limite
    fin = debut + limite - 1
    try:
        res = (
            supabase.table("agent_comments")
            .select("id, agent_id, user_id, contenu, created_at")
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
            .range(debut, fin)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture commentaires agent={agent_id}) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de charger les commentaires pour le moment.")

    lignes = res.data or []

    # Résolution des noms affichés en une seule requête groupée (pas une
    # par commentaire, pour ne pas multiplier les allers-retours Supabase
    # sur une page qui peut afficher jusqu'à 50 commentaires).
    noms_par_user_id = {}
    ids_uniques = list({ligne["user_id"] for ligne in lignes})
    if ids_uniques:
        try:
            profils_res = (
                supabase.table("profiles")
                .select("user_id, nom_affiche")
                .in_("user_id", ids_uniques)
                .execute()
            )
            for p in profils_res.data or []:
                if p.get("nom_affiche"):
                    noms_par_user_id[p["user_id"]] = p["nom_affiche"]
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture noms affichés commentaires agent={agent_id}) : {e}")
            # best-effort : noms_par_user_id reste vide, chaque commentaire
            # retombe sur nom_affiche=None plutôt que de faire échouer
            # tout l'affichage des commentaires.

    return [
        Commentaire(
            id=str(ligne["id"]),
            agent_id=ligne["agent_id"],
            user_id=ligne["user_id"],
            nom_affiche=noms_par_user_id.get(ligne["user_id"]),
            contenu=ligne["contenu"],
            created_at=ligne.get("created_at"),
        )
        for ligne in lignes
    ]


def supprimer_agent_completement(agent_id: str):
    """
    Purge un agent et tout ce qui en dépend directement (documents PDF +
    chunks vectorisés `documents`/`prompts_chunks`, commentaires, notes ;
    `agent_updates` et ses likes/commentaires partent tout seuls via
    ON DELETE CASCADE, voir la migration
    pivot_social_mises_a_jour_agent). Ne vérifie AUCUNE propriété : c'est
    à l'appelant de l'avoir fait avant (voir supprimer_agent ci-dessous et
    api/profiles.py:supprimer_mon_compte, qui appelle ceci pour chaque
    agent du compte).

    Ne touche PAS à `historique_conversations` (journal permanent des
    échanges, jamais purgé ailleurs dans le projet) ni aux notifications
    qui référencent cet agent_id (colonne sans contrainte FK, laissée
    orpheline -- même choix de simplicité que le reste du projet, aucun
    nettoyage de notifications n'existait avant cette fonctionnalité).
    Chaque étape est best-effort (log et continue) sauf la suppression
    finale de la ligne `agents`, qui doit réussir pour que l'appelant
    sache si l'opération a globalement marché.
    """
    try:
        prefixe = f"{agent_id}__"
        for nom_stockage in [f for f in list_documents() if f.startswith(prefixe)]:
            try:
                delete_document(nom_stockage)
            except Exception as e:
                logging.error(f"ERREUR suppression fichier storage {nom_stockage} (agent={agent_id}) : {e}")
    except Exception as e:
        logging.error(f"ERREUR SUPABASE STORAGE (liste documents pour purge agent={agent_id}) : {e}")

    for table in ("documents", "prompts_chunks", "agent_comments", "agent_ratings"):
        try:
            supabase.table(table).delete().eq("agent_id", agent_id).execute()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (purge table {table} pour agent={agent_id}) : {e}")

    supabase.table("agents").delete().eq("id", agent_id).execute()


@router.delete("/{agent_id}", status_code=204)
def supprimer_agent(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    """
    "Supprimer un agent" dans la zone de danger de Mon espace (demande
    Bourama, 2026-07-15). Propriétaire uniquement -- même vérification que
    tous les autres endpoints d'écriture sur un agent.
    """
    try:
        res = supabase.table("agents").select("owner_id").eq("id", agent_id).maybe_single().execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agent {agent_id} avant suppression complète) : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer cet agent pour le moment.")

    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Agent introuvable.")
    if res.data["owner_id"] != utilisateur.id:
        raise HTTPException(status_code=403, detail="Cet agent ne t'appartient pas.")

    try:
        supprimer_agent_completement(agent_id)
    except Exception as e:
        logging.error(f"ERREUR suppression complète agent={agent_id} : {e}")
        raise HTTPException(status_code=500, detail="Impossible de supprimer cet agent pour le moment.")
