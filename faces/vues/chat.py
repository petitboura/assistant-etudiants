"""
Face étudiant — interface Streamlit du coach mathématique.
"""

import sys
import os
import re
import uuid
import json
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
# Le dossier vues/ lui-même : st.navigation() exécute cette page via exec(),
# ce qui n'ajoute PAS automatiquement son propre dossier à sys.path -> sans
# cette ligne, l'import de recuperation_mdp.py juste à côté échoue avec
# ModuleNotFoundError (même cause que pour theme_djiguigne.py ailleurs).
sys.path.append(os.path.dirname(__file__))

import logging
import requests
import streamlit as st
from supabase import create_client
from main import chat
from auth import connexion_depuis_jetons
from connexions.notion import demarrer_connexion_notion, finaliser_connexion_notion, etat_notion_en_attente, est_connecte
from theme_djiguigne import injecter_theme
from recuperation_mdp import gerer_recuperation_mot_de_passe

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


# --- Agent de cette session -----------------------------------------------
# ÉTAPE 1 (généralisation multi-agent) : un seul déploiement doit pouvoir
# servir n'importe quel agent, choisi dynamiquement à chaque visite plutôt
# que figé par déploiement. Ordre de priorité :
#   1. Paramètre d'URL ?agent=... (ex: djiguigne.com/?agent=telecom-ia)
#      -> c'est la voie normale une fois la plateforme en ligne.
#   2. Secret AGENT_ID (ancien comportement, un déploiement = un agent)
#      -> gardé pour ne rien casser sur les déploiements Streamlit Cloud
#      existants (assistant-etudiants, telecom-ia) qui n'ont pas encore
#      été migrés vers des URLs paramétrées.
#   3. "tutorat-maths" en tout dernier recours.
# Doit rester aligné avec AGENT_ID_PAR_DEFAUT de retriever.py / main.py.
def _resoudre_agent_id():
    try:
        agent_depuis_url = st.query_params.get("agent")
    except Exception:
        # st.query_params n'existe que sur des versions récentes de
        # Streamlit ; on retombe silencieusement sur le secret si absent.
        agent_depuis_url = None

    if agent_depuis_url:
        return agent_depuis_url
    return get_secret("AGENT_ID") or "tutorat-maths"


AGENT_ID = _resoudre_agent_id()

# Valeurs affichées si `agents.ui_config` est vide ou injoignable (ex:
# pendant le déploiement du 1er agent, avant remplissage de la colonne).
#
# Pivot social (voir PIVOT_SOCIAL.md, "Ce qui change") : le thème visuel
# par agent est SUPPRIMÉ. Un seul thème fixe pour toute la plateforme
# (voir theme_djiguigne.injecter_theme(), déjà utilisé sur les autres
# pages de l'app — vitrine/créer_agent/mes_agents — mais jamais appliqué
# ici jusqu'à maintenant, c'était le bug). Ce dict ne garde donc QUE le
# contenu propre à chaque agent (texte, emoji), plus aucune couleur,
# police, rayon, ou CSS custom — ces clés-là, même encore présentes dans
# `agents.ui_config` pour d'anciens agents, ne sont simplement plus lues.
UI_CONFIG_PAR_DEFAUT = {
    "titre_page": "Votre coatch mathématique",
    "icone_page": "🎓",
    "titre_accueil": "🎓 Votre coatch mathématique",
    "sous_titre_accueil": "Tout comprendre sur les maths. Je te donne rien, je t'enseigne tout.",
    "emoji_reponse": "🎓",
    "placeholder_saisie": "Pose ta question...",
    # Point 5 (Interface) du cadre de conception. rendu_visuel=True par
    # défaut pour ne pas casser tutorat-maths, qui dépend du rendu LaTeX
    # mais n'a pas cette clé dans sa ligne Supabase (créé avant l'ajout
    # de ce champ). Les nouveaux agents créés via creer_agent.py écrivent
    # explicitement leur propre valeur, donc ce défaut ne s'applique qu'aux
    # agents historiques.
    "rendu_visuel": True,
}


@st.cache_data(ttl=300, show_spinner=False)
def _charger_ui_config(agent_id):
    """
    Lit agents.ui_config pour cet agent. En cas d'échec (secret manquant,
    agent absent, colonne vide) on retombe sur UI_CONFIG_PAR_DEFAUT plutôt
    que de laisser l'app crasher : l'UI reste toujours utilisable, au pire
    avec le texte d'un autre agent (celui codé en dur historiquement).
    """
    config = dict(UI_CONFIG_PAR_DEFAUT)
    try:
        supabase = create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET"))
        res = (
            supabase.table("agents")
            .select("ui_config")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        config.update((res.data or {}).get("ui_config") or {})
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents.ui_config, agent_id={agent_id}) : {e}")
    return config


@st.cache_data(ttl=60, show_spinner=False)
def _agent_est_actif(agent_id):
    """
    ÉTAPE 3 (page "mes agents") : un créateur peut désactiver son agent
    (suppression douce, colonne agents.actif) depuis faces/mes_agents.py.
    Cette fonction empêche l'app de continuer à répondre pour un agent
    désactivé, sinon la désactivation n'aurait aucun effet visible pour
    les utilisateurs finaux.

    Cache court (60s, pas 300s comme ui_config) : une désactivation doit
    prendre effet rapidement, contrairement à un simple changement de
    couleur/texte qui peut attendre un peu.

    Par défaut True en cas d'erreur/colonne absente : on ne veut pas
    qu'une panne Supabase coupe tous les agents existants d'un coup.
    """
    try:
        supabase = create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET"))
        res = (
            supabase.table("agents")
            .select("actif")
            .eq("id", agent_id)
            .maybe_single()
            .execute()
        )
        if res.data is None:
            return True
        return res.data.get("actif", True)
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents.actif, agent_id={agent_id}) : {e}")
        return True


def _lister_conversations_passees(user_id, agent_id):
    """
    Liste des fils de discussion distincts entre CET utilisateur et CET
    agent (2026-07-13, Bourama : liste cliquable dans la sidebar, façon
    Claude.ai -- voir with st.sidebar: plus bas). Titre = début du premier
    message utilisateur du fil (décision de Bourama : pas de titre généré
    par IA, trop coûteux pour ce qu'apporte cette fonctionnalité).

    Un seul aller-retour Supabase (tous les messages user+agent, triés du
    plus ancien au plus récent), regroupés par conversation_id en Python
    -- même stratégie que api/historique.py côté FastAPI, pour un volume
    de données comparable (borné par agent, pas par plateforme entière).

    Les lignes d'avant cette fonctionnalité (conversation_id NULL, jamais
    rattachées à un fil) sont regroupées ensemble sous un fil "Avant
    l'historique par conversation" plutôt qu'ignorées -- elles existent
    et restent consultables, juste pas scindées en fils individuels
    (impossible de savoir rétroactivement où un fil s'arrêtait et où le
    suivant commençait).
    """
    if not user_id:
        return []
    try:
        supabase = create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET"))
        lignes = (
            supabase.table("historique_conversations")
            .select("conversation_id, role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .order("created_at")
            .execute()
        ).data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lister conversations passées, user_id={user_id}, agent_id={agent_id}) : {e}")
        return []

    fils = {}
    for ligne in lignes:
        cle = ligne["conversation_id"] or "legacy"
        if cle not in fils:
            fils[cle] = {"conversation_id": ligne["conversation_id"], "premier_message_user": None, "derniere_activite": ligne["created_at"]}
        if ligne["role"] == "user" and fils[cle]["premier_message_user"] is None:
            fils[cle]["premier_message_user"] = ligne["content"]
        fils[cle]["derniere_activite"] = ligne["created_at"]

    resultat = []
    for cle, fil in fils.items():
        if cle == "legacy":
            titre = "Avant l'historique par conversation"
        else:
            titre = (fil["premier_message_user"] or "Conversation sans titre").strip()
            if len(titre) > 42:
                titre = titre[:42].rstrip() + "…"
        resultat.append({"conversation_id": fil["conversation_id"], "titre": titre, "derniere_activite": fil["derniere_activite"]})

    resultat.sort(key=lambda f: f["derniere_activite"], reverse=True)
    return resultat


def _charger_messages_conversation(user_id, agent_id, conversation_id):
    """
    Recharge le contenu complet d'un fil precis (clic sur une entree de la
    liste ci-dessus), dans le meme format que st.session_state.messages
    ({"role", "content"}). conversation_id peut etre None : recharge alors
    le fil "legacy" (lignes d'avant cette fonctionnalite, jamais rattachees
    a un conversation_id).
    """
    try:
        supabase = create_client(get_secret("SUPABASE_URL"), get_secret("SUPABASE_SECRET"))
        requete = (
            supabase.table("historique_conversations")
            .select("role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
        )
        if conversation_id is None:
            requete = requete.is_("conversation_id", "null")
        else:
            requete = requete.eq("conversation_id", conversation_id)
        lignes = requete.order("created_at").execute().data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (charger conversation, user_id={user_id}, conversation_id={conversation_id}) : {e}")
        return []
    return [{"role": ligne["role"], "content": ligne["content"]} for ligne in lignes]


UI_CONFIG = _charger_ui_config(AGENT_ID)

st.set_page_config(
    page_title=UI_CONFIG["titre_page"],
    page_icon=UI_CONFIG["icone_page"],
    layout="centered",
)

if not _agent_est_actif(AGENT_ID):
    st.warning("Cet agent n'est plus disponible.")
    st.stop()

# Pivot social : thème visuel unique et fixe pour tous les agents (voir
# UI_CONFIG_PAR_DEFAUT plus haut). injecter_theme() est le même appelé
# par vitrine.py/creer_agent.py/mes_agents.py -- une seule palette/police/
# CSS de base pour toute l'app, plus aucune personnalisation par agent.
# Volontairement PAS afficher_entete() ici (qui affiche le logo
# Djiguignè) : le chat reste l'espace de l'agent lui-même, pas celui de
# la marque -- seul le thème (couleurs/police/CSS) est repris, jamais le
# logo.
injecter_theme()

st.markdown("""
    <style>
    /* En-tête natif Streamlit (barre "Share / étoile / crayon / GitHub / ⋮")
       et pied de page ("Made with Streamlit") : supprimés visuellement et
       sans espace réservé.
       ATTENTION (bug identifié en inspectant le bundle JS de Streamlit
       installé, pas une supposition) : le bouton de RÉOUVERTURE de la
       sidebar une fois repliée (icône flèche vers la droite) a pour
       data-testid "stExpandSidebarButton" et vit À L'INTÉRIEUR de
       [data-testid="stToolbar"] -> le display:none qu'on appliquait sur
       stToolbar (comme sur header) supprimait ce bouton avec le reste,
       sans recours (display:none n'est jamais réversible pour un enfant).
       header/stHeader/stToolbar passent donc en visibility:hidden (efface
       tout par défaut, mais réversible), et stExpandSidebarButton +
       stSidebarCollapseButton (le bouton "fermer" quand la sidebar est
       ouverte, vérifié aussi dans le bundle, vit dans stSidebarHeader,
       lui non affecté) sont forcés en visibility:visible juste après. */
    header,
    [data-testid="stHeader"],
    [data-testid="stToolbar"] {
        visibility: hidden !important;
        height: 0 !important;
        min-height: 0 !important;
        background: transparent !important;
    }
    /* Sans en-tête, block-container reprend tout l'espace du haut : on
       enlève le padding réservé pour lui, sinon un vide persiste en haut
       de page même une fois l'en-tête retiré. injecter_theme() met déjà
       2.2rem : on réaligne à la valeur historique du chat (2rem). */
    .block-container {
        padding-top: 2rem !important;
    }

    /* Boutons d'affichage/masquage de la sidebar : stSidebarCollapseButton
       (fermer, visible sidebar ouverte) et stExpandSidebarButton (rouvrir,
       visible sidebar repliée) sont les deux VRAIS data-testid (confirmés
       dans le bundle JS de Streamlit). Rendus visibles + repositionnés en
       fixed, avec un badge de fond neutre + icône blanche forcée, lisibles
       quel que soit le thème (ici fixe et sombre, mais gardé robuste). */
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stExpandSidebarButton"] {
        visibility: visible !important;
        position: fixed !important;
        top: 0.6rem !important;
        left: 0.6rem !important;
        z-index: 999999 !important;
        background-color: rgba(120, 120, 120, 0.35) !important;
        border-radius: 6px !important;
        color: #FFFFFF !important;
    }
    [data-testid="stSidebarCollapseButton"] svg,
    [data-testid="stExpandSidebarButton"] svg {
        visibility: visible !important;
        fill: #FFFFFF !important;
        color: #FFFFFF !important;
    }
    /* Ceinture + bretelles : si l'icône utilise fill="currentColor" sur le
       <path> plutôt que sur le <svg> lui-même, la règle ci-dessus sur le
       <svg> ne suffit pas -- on cible aussi le <path> directement. */
    [data-testid="stSidebarCollapseButton"] svg path,
    [data-testid="stExpandSidebarButton"] svg path {
        fill: #FFFFFF !important;
    }

    /* Barre de saisie (st.chat_input) : par défaut, Streamlit la loge dans
       une bande pleine largeur avec SON PROPRE fond, indépendant du thème
       -> on la fait disparaître (même couleur que le fond de l'app,
       fixe désormais -> var(--dj-fond)) et on donne à l'input lui-même une
       allure de pilule flottante, centrée.
       Note : le nom exact de ce conteneur a changé entre versions de
       Streamlit (stBottomBlockContainer / stChatFloatingInputContainer) ->
       tous les niveaux connus sont ciblés pour rester robuste. */
    [data-testid="stBottom"],
    [data-testid="stBottom"] > div,
    [data-testid="stBottomBlockContainer"],
    .stChatFloatingInputContainer,
    .stChatInputContainer {
        background: var(--dj-fond) !important;
        box-shadow: none !important;
        border-top: none !important;
    }
    /* CORRECTIF (repéré en conditions réelles, capture d'écran) : la
       version précédente posait border + background + border-radius sur
       TROIS niveaux emboîtés à la fois (stChatInput > stChatInputContainer
       > div[data-baseweb="textarea"]) -> chaque niveau dessinait SON PROPRE
       cadre, donnant l'impression de trois pilules imbriquées au lieu
       d'une seule. Un seul niveau (le plus extérieur, stChatInput) porte
       maintenant la pilule visible ; tous les niveaux internes sont
       explicitement rendus transparents et sans bordure pour ne plus
       dessiner leur propre cadre. */
    [data-testid="stChatInput"] {
        background-color: var(--dj-surface) !important;
        border: 1px solid var(--dj-bordure) !important;
        border-radius: 999px !important;
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.28) !important;
        max-width: 720px;
        margin: 0 auto 1.1rem auto !important;
        overflow: hidden;
    }
    [data-testid="stChatInput"] [data-testid="stChatInputContainer"],
    [data-testid="stChatInput"] div[data-baseweb="textarea"] {
        background-color: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }
    /* Bouton d'envoi : gardait son propre fond/bordure par défaut, visible
       comme un carré distinct à droite de la pilule (le "cadre derrière"
       repéré sur la capture). Rendu transparent pour se fondre dans la
       pilule -- seule l'icône flèche reste visible, recolorée ci-dessous. */
    [data-testid="stChatInput"] button {
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
    }
    [data-testid="stChatInput"] button svg {
        fill: var(--dj-texte-muet) !important;
    }
    [data-testid="stChatInput"] button:hover svg {
        fill: var(--dj-accent-1) !important;
    }
    /* Au clic/focus, Streamlit applique sa propre bordure de focus (rouge
       #FF4B4B, sa couleur de marque par défaut) par-dessus la nôtre --
       repéré en conditions réelles. On change juste la couleur de la
       bordure existante, SANS ajouter d'anneau (box-shadow) supplémentaire
       : un ajout précédent créait une double bordure visible (une barre
       décalée derrière la pilule), lui aussi repéré en conditions réelles. */
    [data-testid="stChatInput"]:focus-within {
        border-color: var(--dj-accent-1) !important;
    }
    [data-testid="stChatInput"] textarea {
        background-color: transparent !important;
        color: var(--dj-texte) !important;
    }
    [data-testid="stChatInput"] textarea:focus {
        outline: none !important;
        box-shadow: none !important;
    }
    /* Placeholder ("Pose ta question...") : héritait d'une couleur de
       texte par défaut trop sombre sur le fond quasi-noir de la pilule
       -> quasiment illisible (repéré en conditions réelles). */
    [data-testid="stChatInput"] textarea::placeholder {
        color: var(--dj-texte-muet) !important;
        opacity: 1 !important;
    }

    /* Bulles de message : thème fixe désormais (plus de personnalisation
       par agent). Bulle utilisateur visible (fond surface-haute), bulle
       assistant transparente (comportement historique conservé, juste
       recodé en couleurs fixes). */
    .message-user {
        background-color: var(--dj-surface-haute);
        color: var(--dj-texte);
        padding: 12px 18px;
        border-radius: 18px;
        margin: 8px 0;
        display: inline-block;
        max-width: 75%;
        float: right;
        text-align: right;
        border: 1px solid var(--dj-bordure);
    }

    .message-assistant {
        color: var(--dj-texte);
        background-color: transparent;
        padding: 10px 4px;
        margin: 8px 0;
        max-width: 85%;
        line-height: 1.7;
        border-radius: 18px;
    }

    .clearfix { clear: both; }

    .statut-outil {
        font-style: italic;
        font-size: 0.85em;
        color: var(--dj-texte-muet);
        padding: 4px 4px;
        margin: 4px 0 0 0;
    }

    a { color: var(--dj-accent-1); }
    </style>
""", unsafe_allow_html=True)


def _normaliser_latex(texte):
    """
    Le moteur Markdown de Streamlit traite `\\(`, `\\)`, `\\[`, `\\]` comme des
    caractères échappés et supprime le backslash avant même que MathJax ne
    voie le texte. On convertit donc ces délimiteurs LaTeX vers `$ $` et
    `$$ $$`, que Markdown laisse intacts (le `$` n'a pas de sens spécial
    pour lui).
    """
    texte = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', texte, flags=re.DOTALL)
    texte = re.sub(r'\\\((.*?)\\\)', r'$\1$', texte, flags=re.DOTALL)
    return texte


def _typeset_mathjax():
    """
    Les <script> injectés via st.markdown(unsafe_allow_html=True) ne
    s'exécutent JAMAIS (limitation du DOM : les scripts insérés via
    innerHTML ne sont pas exécutés par le navigateur). On passe donc par
    un composant Streamlit (rendu dans une vraie page HTML, où les
    scripts s'exécutent normalement) qui va lui-même injecter MathJax
    dans la page PARENTE (window.parent), puis demander le rendu des
    formules déjà présentes dans le DOM.
    """
    # st.components.v1.html est deprecie (suppression prevue apres le
    # 1er juin 2026) au profit de st.iframe, qui accepte directement une
    # chaine HTML/JS de la meme facon.
    st.iframe(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const win = window.parent;

            function typeset() {
                if (win.MathJax && win.MathJax.typesetPromise) {
                    win.MathJax.typesetPromise();
                }
            }

            if (!win.MathJax) {
                win.MathJax = {
                    tex: {
                        inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                        displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
                    },
                    svg: { fontCache: 'global' },
                    startup: {
                        ready: function() {
                            MathJax.startup.defaultReady();
                            MathJax.startup.promise.then(typeset);
                        }
                    }
                };
                const script = doc.createElement('script');
                script.src = "https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js";
                script.async = true;
                doc.head.appendChild(script);
            } else {
                typeset();
            }
        })();
        </script>
        """,
        height=1,
    )


# --- Compte étudiant (optionnel) ---------------------------------------
# Le chat reste utilisable sans connexion. Ce bloc, dans la barre latérale,
# ne bloque jamais l'accès au coach : il propose juste de se connecter pour
# celles et ceux qui en ont besoin (ex: plus tard, connecter son Notion).

if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

# Doit passer avant tout le reste (y compris le retour OAuth Notion
# juste en dessous) : si l'URL contient les tokens d'un lien "mot de passe
# oublié", on affiche uniquement le formulaire de nouveau mot de passe et
# on s'arrête là, plutôt que de rendre le chat en même temps dans le
# corps de la page pendant que la sidebar affiche ce formulaire.
if gerer_recuperation_mot_de_passe():
    st.stop()

# --- Retour de redirection OAuth (Notion) -------------------------------
# Meme URL de callback que Google (URL_RETOUR_APP) : on distingue les deux
# en verifiant si le `state` recu correspond a une tentative Notion en
# attente. Si oui, on finalise et on nettoie l'URL. Si non (ex: retour
# Google, pas encore branche dans cette interface), on ne touche a rien.
_params = st.query_params
if "code" in _params and "state" in _params and etat_notion_en_attente(_params["state"]):
    succes, message = finaliser_connexion_notion(_params["code"], _params["state"])
    st.query_params.clear()
    if succes:
        st.session_state.notion_message = f"✅ Notion connecté ({message})."
    else:
        st.session_state.notion_message = f"❌ {message}"
    st.rerun()

# --- Connexion automatique depuis la plateforme -------------------------
# Ajouté le 2026-07-12 (Bourama : "dès que tu crées un compte à la
# plateforme, tu es automatiquement connecté à tous les agents dans la
# plateforme, sans exception"). Le compte est déjà unifié côté base de
# données (comme pour Notion : une connexion vaut pour tous les agents) --
# ce qui manquait, c'était un pont technique entre la session Supabase de
# la plateforme Next.js et chat.py, qui n'a aucun moyen natif de la voir
# (origine différente, pas de cookie/stockage partagé).
#
# Le pont : components/BoutonUtiliser.tsx transmet access_token et
# refresh_token de la session Next.js en cours dans l'URL qui ouvre le
# chat, si la personne est déjà connectée sur la plateforme. On échange
# ces jetons contre une session Streamlit valide ici, sans redemander ni
# email ni mot de passe -- quel que soit l'agent ouvert, sans exception.
if (
    "access_token" in _params
    and "refresh_token" in _params
    and st.session_state.session_utilisateur is None
):
    _succes_jetons, _resultat_jetons = connexion_depuis_jetons(
        _params["access_token"], _params["refresh_token"]
    )
    if _succes_jetons:
        st.session_state.session_utilisateur = _resultat_jetons
    # BUG CORRIGÉ le 2026-07-12 (repéré en conditions réelles par Bourama :
    # "ça pointe vers un autre dépôt streamlit" -- en réalité un AUTRE
    # AGENT, pas un autre déploiement). st.query_params.clear() effaçait
    # TOUS les paramètres, y compris `agent` -- au st.rerun() qui suit,
    # Streamlit réexécute le script depuis le début, _resoudre_agent_id()
    # ne retrouve plus `agent` dans l'URL et retombe sur l'agent par
    # défaut (tutorat-maths), quel que soit l'agent réellement ouvert. On
    # retire donc UNIQUEMENT les jetons, en préservant tout le reste
    # (`agent` en particulier).
    del st.query_params["access_token"]
    del st.query_params["refresh_token"]
    st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

# Identifiant du fil de discussion actif (2026-07-13, Bourama : liste de
# conversations distinctes et cliquables dans la sidebar, façon Claude.ai
# -- voir with st.sidebar: juste en dessous, qui en a besoin). Généré une
# seule fois par fil (pas par message), transmis tel quel à chat() puis à
# historique_conversations.conversation_id. Un nouvel identifiant est créé
# soit ici au tout premier chargement, soit explicitement via le bouton
# "Nouvelle conversation" plus bas, soit en rechargeant un ancien fil
# depuis la liste (voir plus bas). Initialisé ICI, avant la sidebar (et
# pas avec compteur/confirmation_en_attente plus bas) : la sidebar en a
# besoin immédiatement pour afficher "Nouvelle conversation" et la liste
# des fils passés.
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = str(uuid.uuid4())

with st.sidebar:
    # Lien de retour vers la page agent Next.js (2026-07-12, Bourama : "il
    # n'y a pas de quitter plein écran qui va faire retour où tu étais" --
    # le bouton "Plein écran" de components/BoutonUtiliser.tsx ouvre cette
    # page Streamlit en nouvel onglet SANS aucun moyen d'y revenir ensuite).
    # Déplacé dans la sidebar le 2026-07-12 (Bourama : ça ne doit apparaître
    # que dans le volet de gauche, pas dans le corps principal, et rester
    # minimaliste -- ce n'est pas un sujet principal de la page).
    # `URL_PLATEFORME` optionnel (comme URL_RETOUR_APP) : si absent, on
    # n'affiche simplement pas le lien plutôt que de planter.
    #
    # Texte forcé en noir (2026-07-13, Bourama, capture d'écran) : la
    # règle générale ".stLinkButton > a { color: #1A0D02 !important }"
    # dans theme_djiguigne.py ne suffisait pas -- Streamlit enveloppe le
    # texte du lien dans un <p> interne, et la règle générale
    # "p, span, label { color: var(--dj-texte) }" (texte crème clair)
    # s'applique DIRECTEMENT à ce <p>, qui gagne sur la couleur héritée du
    # <a> parent même si celle du <a> est en !important -- un !important
    # ne gouverne que la propriété de SON PROPRE élément, pas celle d'un
    # enfant qui a sa propre règle explicite. Voir le correctif ajouté
    # dans theme_djiguigne.py (nouvelle règle ciblant spécifiquement les
    # <p>/<span> à l'intérieur de .stLinkButton > a).
    _url_plateforme = get_secret("URL_PLATEFORME")
    if _url_plateforme:
        st.link_button(
            "Retour à l'agent",
            f"{_url_plateforme.rstrip('/')}/agent/{AGENT_ID}",
            icon=":material/arrow_back:",
        )

    # Historique par fils de discussion, façon Claude.ai (2026-07-13,
    # Bourama, capture d'écran de la liste "Discussions" de Claude.ai à
    # l'appui). Ne nécessite PAS d'être connecté pour DISCUTER (comme tout
    # le reste du chat), mais nécessite un user_id pour lire/écrire
    # l'historique (colonne user_id NOT NULL sur historique_conversations)
    # -- calculé ici directement plutôt que d'attendre `user_id_courant`
    # plus bas dans le fichier, pas encore défini à ce stade du script.
    _user_id_historique = (
        st.session_state.session_utilisateur.user.id
        if st.session_state.session_utilisateur else None
    )

    if _user_id_historique:
        # "Nouvelle conversation" sans le dégradé orange (2026-07-13,
        # Bourama : "enlever les couleurs pour nouvelle conversation") :
        # .stButton cible TOUS les boutons standards via le CSS global de
        # theme_djiguigne.py (voir son commentaire "mêmes valeurs que le
        # bouton .stButton standard" plus haut) -- st.container(key=...)
        # ajoute une classe stable ".st-key-<key>" (Streamlit >= 1.37) sur
        # son wrapper, ce qui permet de neutraliser LE dégradé UNIQUEMENT
        # pour ce bouton précis sans toucher aux autres. N'a de sens que
        # s'il y a quelque chose à quitter -- pas affiché sur un fil déjà
        # vierge, ça n'aurait rien à faire de plus qu'un bouton qui ne
        # fait visiblement rien.
        if st.session_state.messages:
            with st.container(key="bouton_nouvelle_conversation"):
                st.markdown(
                    """<style>
                    .st-key-bouton_nouvelle_conversation .stButton > button {
                        background: var(--dj-surface-haute) !important;
                        color: var(--dj-texte) !important;
                        box-shadow: none !important;
                        border: 1px solid var(--dj-bordure) !important;
                    }
                    </style>""",
                    unsafe_allow_html=True,
                )
                if st.button("Nouvelle conversation", icon=":material/add_comment:"):
                    st.session_state.messages = []
                    st.session_state.conversation_id = str(uuid.uuid4())
                    st.rerun()

        _conversations_passees = _lister_conversations_passees(_user_id_historique, AGENT_ID)
        if _conversations_passees:
            # Volet qui se referme après un clic sur une ancienne
            # conversation (2026-07-13, Bourama : "les volets se ferment
            # automatiquement"). st.expander n'a pas d'état contrôlable
            # après coup en une seule passe Streamlit -- son "expanded="
            # ne peut être positionné qu'AVANT le rerun qui suit le clic,
            # donc on le pilote depuis session_state, mis à jour juste
            # avant le st.rerun() du bloc historique ci-dessous (pas ici).
            _cle_ouvert = "historique_expander_ouvert"
            with st.expander(
                "Historique",
                icon=":material/history:",
                expanded=st.session_state.get(_cle_ouvert, False),
            ):
                # Boutons sans effet bouton (2026-07-13, Bourama : "enlever
                # l'effet bouton des anciennes conversations et faire texte
                # libre séparé par des lignes quasi invisibles"). Même
                # technique de scoping que "Nouvelle conversation"
                # ci-dessus (st.container(key=...) + classe
                # ".st-key-...") : fond transparent, pas de bordure ni
                # d'ombre, texte aligné à gauche, séparateur presque
                # invisible entre chaque ligne (rgba très faible plutôt
                # qu'une vraie bordure visible).
                with st.container(key="liste_historique_conversations"):
                    st.markdown(
                        """<style>
                        .st-key-liste_historique_conversations .stButton > button {
                            background: transparent !important;
                            color: var(--dj-texte) !important;
                            box-shadow: none !important;
                            border: none !important;
                            border-bottom: 1px solid rgba(255,255,255,0.06) !important;
                            border-radius: 0 !important;
                            font-weight: 400 !important;
                            text-align: left !important;
                            justify-content: flex-start !important;
                            padding: 0.5rem 0.2rem !important;
                        }
                        .st-key-liste_historique_conversations .stButton > button:hover {
                            transform: none !important;
                            box-shadow: none !important;
                            color: var(--dj-accent-1) !important;
                        }
                        .st-key-liste_historique_conversations .stButton > button:disabled {
                            color: var(--dj-accent-1) !important;
                            opacity: 1 !important;
                        }
                        </style>""",
                        unsafe_allow_html=True,
                    )
                    for _conv in _conversations_passees:
                        _est_active = _conv["conversation_id"] == st.session_state.conversation_id
                        _libelle = ("● " if _est_active else "") + _conv["titre"]
                        if st.button(
                            _libelle,
                            key=f"conv_{_conv['conversation_id'] or 'legacy'}",
                            disabled=_est_active,
                        ):
                            st.session_state.messages = _charger_messages_conversation(
                                _user_id_historique, AGENT_ID, _conv["conversation_id"]
                            )
                            st.session_state.conversation_id = _conv["conversation_id"] or str(uuid.uuid4())
                            # Referme le volet Historique au clic (voir
                            # commentaire plus haut) : positionné avant le
                            # rerun, donc pris en compte au prochain
                            # rendu du st.expander ci-dessus.
                            st.session_state[_cle_ouvert] = False
                            st.rerun()

    # Bloc compte (connexion/inscription) + connexion Notion retiré du
    # panneau latéral le 2026-07-12 (Bourama : "plus de se connecter, plus
    # de notion connectée, plus de connecter un outil, juste ce qu'il y
    # a"). Le chat reste utilisable sans aucun compte, comme avant.
    #
    # CONSÉQUENCE À NOTER : la section "Avis sur cet agent" juste en
    # dessous (notes/commentaires) exigeait d'être connecté -- sans ce
    # bloc, il n'existe plus AUCUN moyen de se connecter depuis chat.py,
    # donc cette section affichera en permanence "Connecte-toi ci-dessus"
    # sans qu'aucun "ci-dessus" n'existe plus pour le faire. Signalé à
    # Bourama, pas corrigé ici -- pas demandé dans cette instruction.


    # Notes + commentaires (2026-07-12, Bourama : "je veux que ce soit dans
    # le tableau de bord du chat lui-même pour que quelqu'un qui a ouvert
    # en plein écran puisse directement commenter ou donner des étoiles" --
    # ces fonctionnalités existaient déjà côté Next.js
    # (components/NoteAgent.tsx, CommentairesAgent.tsx sur /agent/[id]),
    # mais invisibles pour quelqu'un qui ouvre le chat en "Plein écran"
    # (nouvel onglet, uniquement cette page Streamlit, jamais la page
    # Next.js qui les contient). Appelle les MÊMES endpoints
    # (POST/GET /api/agents/{id}/rating et .../comments) pour que la note/
    # les commentaires soient identiques, peu importe par où ils ont été
    # postés.
    # Déplacé dans la sidebar (repliée par défaut, st.expander) le
    # 2026-07-12 : Bourama a précisé que ça doit rester minimaliste, pas un
    # sujet principal de la page -- uniquement visible en ouvrant le volet
    # de gauche, comme le reste du compte/Notion juste au-dessus.
    #
    # `URL_API` optionnel (même convention que URL_PLATEFORME plus haut) :
    # si absent, cette section ne s'affiche simplement pas plutôt que de
    # planter.
    _url_api = get_secret("URL_API")
    if _url_api:
        _url_api = _url_api.rstrip("/")
        with st.expander("Avis sur cet agent", icon=":material/rate_review:"):
            if st.session_state.session_utilisateur is None:
                st.caption("Connecte-toi ci-dessus pour noter ou commenter.")
            else:
                _jeton = st.session_state.session_utilisateur.access_token
                _entetes = {"Authorization": f"Bearer {_jeton}"}

                # Étoiles : st.feedback("stars") renvoie un index 0-4 (5
                # étoiles), ou None tant que rien n'est cliqué -- +1 pour
                # matcher la note 1-5 attendue par l'API (voir
                # NoterAgentPayload, api/agents.py). Un flag en
                # session_state évite de renvoyer le POST à chaque rerun
                # Streamlit tant que la note affichée n'a pas changé.
                _cle_derniere_note = f"derniere_note_envoyee_{AGENT_ID}"
                note_choisie = st.feedback("stars")
                if note_choisie is not None:
                    note_finale = note_choisie + 1
                    if st.session_state.get(_cle_derniere_note) != note_finale:
                        try:
                            requests.post(
                                f"{_url_api}/api/agents/{AGENT_ID}/rating",
                                json={"note": note_finale},
                                headers=_entetes,
                                timeout=10,
                            ).raise_for_status()
                            st.session_state[_cle_derniere_note] = note_finale
                            st.toast(f"Note enregistrée : {note_finale}/5")
                        except Exception as e:
                            logging.error(f"ERREUR API (POST rating, agent_id={AGENT_ID}) : {e}")
                            st.warning("Impossible d'enregistrer la note pour le moment.")

                nouveau_commentaire = st.text_area(
                    "Ajouter un commentaire", key="nouveau_commentaire_chat"
                )
                if st.button("Publier le commentaire"):
                    if nouveau_commentaire.strip():
                        try:
                            requests.post(
                                f"{_url_api}/api/agents/{AGENT_ID}/comments",
                                json={"contenu": nouveau_commentaire.strip()},
                                headers=_entetes,
                                timeout=10,
                            ).raise_for_status()
                            st.success("Commentaire publié.")
                            st.rerun()
                        except Exception as e:
                            logging.error(f"ERREUR API (POST comments, agent_id={AGENT_ID}) : {e}")
                            st.warning("Impossible de publier le commentaire pour le moment.")
                    else:
                        st.warning("Le commentaire ne peut pas être vide.")

            # Liste des commentaires existants : public, pas besoin de
            # connexion pour les lire (même endpoint que
            # CommentairesAgent.tsx).
            try:
                _reponse_commentaires = requests.get(
                    f"{_url_api}/api/agents/{AGENT_ID}/comments", timeout=10
                )
                _reponse_commentaires.raise_for_status()
                for _commentaire in _reponse_commentaires.json():
                    _auteur = _commentaire.get("nom_affiche") or "Utilisateur"
                    st.caption(f"**{_auteur}** — {_commentaire['contenu']}")
            except Exception as e:
                logging.error(f"ERREUR API (GET comments, agent_id={AGENT_ID}) : {e}")
                st.caption("Impossible de charger les commentaires pour le moment.")

    # Bouton partager (2026-07-12, Bourama : "il faut un bouton
    # partager... dans le chat"). Déplacé sous "Avis sur cet agent"
    # (2026-07-13, Bourama : "emmener partager en dessous de avis") --
    # dernier élément de la sidebar désormais, plus juste après "Retour à
    # l'agent" comme avant.
    #
    # VRAI partage natif, identique à components/BoutonPartager.tsx côté
    # Next.js : Web Share API (navigator.share -- ouvre le sélecteur natif
    # du système, SMS/WhatsApp/etc.) si le navigateur le supporte, sinon
    # copie presse-papiers avec confirmation "Copié !", sinon
    # window.prompt en tout dernier recours.
    #
    # Streamlit n'expose aucune de ces API navigateur en Python -- même
    # technique que _typeset_mathjax() plus haut : un <script> injecté via
    # st.markdown ne s'exécute JAMAIS (limitation du DOM), donc on passe
    # par st.iframe (vraie page HTML, où les scripts s'exécutent
    # normalement), qui appelle window.parent.navigator (celui de la
    # VRAIE page, pas de l'iframe) -- l'iframe elle-même n'a pas la
    # permission "web-share".
    #
    # Couleurs codées en dur (pas de var(--dj-*)) : un iframe srcdoc est un
    # document séparé, il n'hérite JAMAIS des variables CSS injectées dans
    # le document parent -- copiées telles quelles depuis
    # theme_djiguigne.py (mêmes valeurs que le bouton .stButton standard,
    # pour rester visuellement identique).
    #
    # Bande blanche à côté du bouton corrigée (2026-07-13, Bourama,
    # capture d'écran) : c'était la barre de défilement par défaut de
    # l'iframe, déclenchée par un léger dépassement de hauteur (le corps
    # HTML par défaut a une marge de 8px que rien ne retirait, donc le
    # contenu dépassait très légèrement les 48px demandés). Corrigé par un
    # reset margin/padding/overflow sur html,body dans le document iframe.
    # `scrolling=False` tenté d'abord (2026-07-13) puis RETIRÉ aussitôt :
    # cette version de Streamlit (voir requirements.txt, non pinnée) ne
    # supporte pas ce paramètre sur st.iframe -- "TypeError:
    # IframeMixin.iframe() got an unexpected keyword argument 'scrolling'",
    # confirmé par capture d'écran de l'erreur en prod. Le reset CSS seul
    # suffit à empêcher le débordement qui déclenchait la barre.
    if _url_plateforme:
        _url_partage = f"{_url_plateforme.rstrip('/')}/agent/{AGENT_ID}"
        _titre_partage = UI_CONFIG["titre_page"]
        st.iframe(
            f"""
            <html><body style="margin:0;padding:0;overflow:hidden;">
            <button id="btn-partager" style="
                width: 100%; box-sizing: border-box;
                background: linear-gradient(135deg, #F2A65A 0%, #D9631F 55%, #8A2E0A 100%);
                color: #1A0D02; font-weight: 700; font-family: Inter, sans-serif;
                font-size: 0.9rem; border: none; border-radius: 10px;
                padding: 0.55rem 1.1rem; cursor: pointer;
                display: flex; align-items: center; justify-content: center; gap: 0.5rem;
                box-shadow: 0 2px 14px rgba(217,99,31,0.25);
                transition: transform 0.15s ease;
            " onmouseover="this.style.transform='translateY(-2px)'" onmouseout="this.style.transform='translateY(0)'">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="#1A0D02">
                    <path d="M18 16.08c-.76 0-1.44.3-1.96.77L8.91 12.7c.05-.23.09-.46.09-.7s-.04-.47-.09-.7l7.05-4.11c.54.5 1.25.81 2.04.81 1.66 0 3-1.34 3-3s-1.34-3-3-3-3 1.34-3 3c0 .24.04.47.09.7L8.04 9.81C7.5 9.31 6.79 9 6 9c-1.66 0-3 1.34-3 3s1.34 3 3 3c.79 0 1.5-.31 2.04-.81l7.12 4.16c-.05.21-.08.43-.08.65 0 1.61 1.31 2.92 2.92 2.92 1.61 0 2.92-1.31 2.92-2.92s-1.31-2.92-2.92-2.92z"/>
                </svg>
                <span id="btn-partager-texte">Partager</span>
            </button>
            </body></html>
            <script>
                document.getElementById('btn-partager').addEventListener('click', async function() {{
                    const url = {json.dumps(_url_partage)};
                    const titre = {json.dumps(_titre_partage)};
                    const texte = document.getElementById('btn-partager-texte');
                    const nav = window.parent.navigator;
                    if (nav.share) {{
                        try {{ await nav.share({{ title: titre, url: url }}); }} catch (e) {{
                            // Annulé par la personne ou échec silencieux du
                            // sélecteur natif -- flux normal du Web Share
                            // API, pas une erreur à afficher.
                        }}
                        return;
                    }}
                    try {{
                        await nav.clipboard.writeText(url);
                        texte.textContent = 'Copié !';
                        setTimeout(function() {{ texte.textContent = 'Partager'; }}, 2000);
                    }} catch (e) {{
                        window.parent.prompt('Copie ce lien :', url);
                    }}
                }});
            </script>
            """,
            height=52,
        )



def _afficher_arguments(arguments):
    if not arguments:
        return ""
    lignes = [f"- **{cle}** : {valeur}" for cle, valeur in arguments.items()]
    return "\n".join(lignes)


def _consommer_flux(generateur, placeholder_statut, placeholder, reponse_deja=""):
    """
    Consomme un flux d'évènements renvoyé par chat(). Met à jour les
    placeholders au fur et à mesure. S'arrête dès qu'une confirmation est
    demandée (outil sensible) : dans ce cas on ne considère PAS la réponse
    comme terminée, on retourne l'évènement de confirmation pour que
    l'appelant affiche les boutons Confirmer/Annuler.

    Retourne (reponse_complete, evenement_confirmation_ou_None).
    """
    reponse_complete = reponse_deja

    for evenement in generateur:
        type_evenement = evenement.get("type")
        texte = evenement.get("texte", "")

        if type_evenement in ("statut", "statut_termine"):
            placeholder_statut.markdown(
                f'<div class="statut-outil">{texte}</div>',
                unsafe_allow_html=True
            )
        elif type_evenement == "reponse":
            if reponse_complete == "":
                placeholder_statut.empty()
            reponse_complete += texte
            contenu_affiche = _normaliser_latex(reponse_complete)
            placeholder.markdown(
                f'<div class="message-assistant">{contenu_affiche}{UI_CONFIG["emoji_reponse"]}</div><div class="clearfix"></div>',
                unsafe_allow_html=True
            )
        elif type_evenement == "confirmation_requise":
            placeholder_statut.empty()
            return reponse_complete, evenement

    return reponse_complete, None


if "compteur" not in st.session_state:
    st.session_state.compteur = 0

if "confirmation_en_attente" not in st.session_state:
    st.session_state.confirmation_en_attente = None

# --- Limite visiteur non connecté (Étape B.3, pivot social) -------------
# Quelqu'un qui reçoit un lien vers cet agent et discute sans compte peut
# poser un nombre limité de questions avant d'être invité à s'inscrire.
# Décision de Bourama (2026-07-11) : entre 3 et 5, valeur volontairement
# isolée dans une constante pour rester facile à ajuster.
SEUIL_VISITEUR_NON_CONNECTE = 4

if "compteur_visiteur" not in st.session_state:
    st.session_state.compteur_visiteur = 0


def _rendre_titre_accueil(ui_config):
    """
    Pivot social : plus de personnalisation de couleur par agent (mode
    multicolore "logo" retiré, voir PIVOT_SOCIAL.md). st.title() suffit
    désormais -- il rend un <h1>, déjà stylé (police, couleur) par la
    règle "h1, h2, h3, .dj-display" du thème fixe injecté plus haut
    (injecter_theme()), donc rien à recoder ici.
    """
    st.title(ui_config["titre_accueil"])


if len(st.session_state.messages) == 0:
    _rendre_titre_accueil(UI_CONFIG)
    st.caption(UI_CONFIG["sous_titre_accueil"])

for message in st.session_state.messages:
    if message["role"] == "user":
        st.markdown(f'<div class="message-user">{message["content"]}</div><div class="clearfix"></div>', unsafe_allow_html=True)
    else:
        contenu_affiche = _normaliser_latex(message["content"])
        st.markdown(f'<div class="message-assistant">{contenu_affiche}</div><div class="clearfix"></div>', unsafe_allow_html=True)

# --- Confirmation d'un outil sensible en attente ------------------------
# Si le tour precedent s'est arrete sur une demande de confirmation (ex :
# le modele veut creer une page Notion), on affiche l'action proposee et
# on bloque toute nouvelle question tant que l'etudiant n'a pas choisi.
if st.session_state.confirmation_en_attente is not None:
    evenement_attente = st.session_state.confirmation_en_attente
    nom_lisible = evenement_attente["nom_lisible"]
    arguments = evenement_attente["arguments"]

    st.warning(
        f"🔒 L'assistant souhaite effectuer une action qui modifie ton Notion : "
        f"**{nom_lisible}**"
    )
    details = _afficher_arguments(arguments)
    if details:
        st.markdown(details)

    colonne_confirmer, colonne_annuler = st.columns(2)
    decision = None
    if colonne_confirmer.button("Confirmer", key="confirmer_outil", icon=":material/check:"):
        decision = True
    if colonne_annuler.button("Annuler", key="annuler_outil", icon=":material/close:"):
        decision = False

    if decision is not None:
        placeholder_statut = st.empty()
        placeholder = st.empty()

        try:
            generateur = chat(reprise={
                "etat_reprise": evenement_attente["etat_reprise"],
                "approuve": decision,
            })
            reponse_complete, nouvelle_attente = _consommer_flux(generateur, placeholder_statut, placeholder)
        except Exception as e:
            # Ne doit normalement pas arriver (chat() catche déjà ses propres
            # erreurs API), mais on préfère un message propre à un crash
            # Streamlit si un cas imprévu remonte quand même (ex: bug dans
            # la construction de l'état de reprise).
            logging.error(f"ERREUR INATTENDUE (reprise chat(), agent_id={AGENT_ID}) : {e}")
            placeholder_statut.empty()
            placeholder.error("Désolé, une erreur inattendue est survenue. Merci de réessayer.")
            reponse_complete, nouvelle_attente = "", None

        st.session_state.confirmation_en_attente = nouvelle_attente

        if nouvelle_attente is None:
            contenu_affiche = _normaliser_latex(reponse_complete)
            placeholder.markdown(
                f'<div class="message-assistant">{contenu_affiche}</div><div class="clearfix"></div>',
                unsafe_allow_html=True
            )
            st.session_state.messages.append({"role": "assistant", "content": reponse_complete})

        st.rerun()

    st.stop()


user_id_courant = (
    st.session_state.session_utilisateur.user.id
    if st.session_state.session_utilisateur else None
)

_visiteur_bloque = (
    user_id_courant is None
    and st.session_state.compteur_visiteur >= SEUIL_VISITEUR_NON_CONNECTE
)

if _visiteur_bloque:
    # Corrigé le 2026-07-12 (Bourama) : ce lien utilisait URL_RETOUR_APP,
    # qui est l'URL de CE déploiement Streamlit (sert au retour OAuth
    # Notion/Google), pas celle de la plateforme Next.js -- il pointait
    # donc vers une page /connexion qui n'existe pas côté Streamlit.
    # URL_PLATEFORME (déjà utilisé plus haut pour "← Retour à l'agent")
    # est la bonne variable ici.
    _url_plateforme_inscription = get_secret("URL_PLATEFORME")
    _lien_connexion = (
        f"{_url_plateforme_inscription.rstrip('/')}/connexion"
        if _url_plateforme_inscription else None
    )
    st.info(
        "Tu as atteint la limite de messages en tant que visiteur non "
        "connecté. Crée un compte gratuitement pour continuer à discuter "
        "avec cet agent."
    )
    if _lien_connexion:
        st.link_button("Se connecter / Créer un compte", _lien_connexion)
    st.chat_input(UI_CONFIG["placeholder_saisie"], disabled=True)
elif prompt := st.chat_input(UI_CONFIG["placeholder_saisie"]):
    st.session_state.compteur += 1
    if user_id_courant is None:
        st.session_state.compteur_visiteur += 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.markdown(f'<div class="message-user">{prompt}</div><div class="clearfix"></div>', unsafe_allow_html=True)

    historique = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]

    placeholder_statut = st.empty()
    placeholder = st.empty()

    try:
        generateur = chat(prompt, historique, user_id_courant, agent_id=AGENT_ID, conversation_id=st.session_state.conversation_id)
        reponse_complete, evenement_confirmation = _consommer_flux(generateur, placeholder_statut, placeholder)
    except Exception as e:
        # Idem : chat() catche déjà ses erreurs API en interne (cascade
        # Groq -> Gemini), ce try/except couvre uniquement l'imprévu.
        logging.error(f"ERREUR INATTENDUE (chat(), agent_id={AGENT_ID}) : {e}")
        placeholder_statut.empty()
        placeholder.error("Désolé, une erreur inattendue est survenue. Merci de réessayer.")
        reponse_complete, evenement_confirmation = "", None

    if evenement_confirmation is not None:
        # On s'arrete ici : la reponse n'est pas encore terminee, il faut
        # d'abord que l'etudiant confirme ou annule l'action proposee.
        st.session_state.confirmation_en_attente = evenement_confirmation
        st.rerun()

    contenu_affiche = _normaliser_latex(reponse_complete)
    placeholder.markdown(
        f'<div class="message-assistant">{contenu_affiche}</div><div class="clearfix"></div>',
        unsafe_allow_html=True
    )

    st.session_state.messages.append({"role": "assistant", "content": reponse_complete})

# Toujours en dernier : (re)déclenche le rendu MathJax sur tout ce qui
# vient d'être affiché (historique + nouvelle réponse le cas échéant).
#
# Bug corrigé le 2026-07-12 (remonté par Bourama, capture d'écran d'un
# agent créé avant le pivot social — "Mathématique" — qui affichait le
# LaTeX brut au lieu de le rendre) : ce bloc était conditionné à
# UI_CONFIG["rendu_visuel"], une case à cocher qui existait dans l'ancien
# formulaire Streamlit (faces/vues/creer_agent.py, valeur par défaut
# `False`) et que le nouveau formulaire Next.js (api/agents.py, Étape D.6
# du pivot social) n'expose plus du tout — un nouvel agent hérite du
# défaut `True` de UI_CONFIG_PAR_DEFAUT, mais un agent créé AVANT le
# pivot avec la case décochée garde son `rendu_visuel: False` explicite
# en base, et ce défaut ne l'écrase pas (voir _charger_ui_config,
# `config.update(...)` ne fait que fusionner, pas remplacer). Demande de
# Bourama : plus de toggle du tout, MathJax tourne pour TOUS les agents,
# anciens comme nouveaux — appel inconditionnel plutôt que de corriger la
# donnée existante en base agent par agent.
_typeset_mathjax()

# Bloc "Remplir le formulaire" (feedback Google Forms) retiré le 2026-07-12
# (Bourama : résidu à enlever, plus d'usage).
#
# Bloc "Notes + commentaires" retiré d'ici le 2026-07-12 (déplacé dans la
# sidebar, en version compacte -- voir with st.sidebar: plus haut, juste
# après le bouton "Se déconnecter"). Bourama : ça ne doit apparaître que
# dans le volet de gauche, pas comme sujet principal de la page.
