"""
Face étudiant — interface Streamlit du coach mathématique.
"""

import sys
import os
import re
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
# Le dossier vues/ lui-même : st.navigation() exécute cette page via exec(),
# ce qui n'ajoute PAS automatiquement son propre dossier à sys.path -> sans
# cette ligne, l'import de recuperation_mdp.py juste à côté échoue avec
# ModuleNotFoundError (même cause que pour theme_djiguigne.py ailleurs).
sys.path.append(os.path.dirname(__file__))

import logging
import streamlit as st
from supabase import create_client
from main import chat
from auth import inscription, connexion, deconnexion, demarrer_reinitialisation_mot_de_passe
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
    [data-testid="stChatInput"] {
        background-color: var(--dj-surface) !important;
        border: 1px solid var(--dj-bordure) !important;
        border-radius: 999px !important;
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.28) !important;
        max-width: 720px;
        margin: 0 auto 1.1rem auto !important;
    }
    /* Au clic/focus, Streamlit applique sa propre bordure de focus (rouge
       #FF4B4B, sa couleur de marque par défaut) par-dessus la nôtre --
       repéré en conditions réelles (capture d'écran), pas juste supposé.
       On la remplace explicitement par l'accent du thème. */
    [data-testid="stChatInput"]:focus-within {
        border-color: var(--dj-accent-1) !important;
        box-shadow: 0 0 0 1px var(--dj-accent-1), 0 6px 24px rgba(0, 0, 0, 0.28) !important;
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

with st.sidebar:
    if st.session_state.session_utilisateur is None:
        st.markdown("### Compte (optionnel)")
        st.caption(
            "Le chat fonctionne sans compte. Connecte-toi seulement si tu "
            "veux débloquer des fonctionnalités qui en ont besoin."
        )

        onglet_connexion, onglet_inscription = st.tabs(["Se connecter", "Créer un compte"])

        with onglet_connexion:
            email_connexion = st.text_input("Email", key="email_connexion")
            mdp_connexion = st.text_input("Mot de passe", type="password", key="mdp_connexion")
            if st.button("Se connecter", key="bouton_connexion"):
                succes, resultat = connexion(email_connexion, mdp_connexion)
                if succes:
                    st.session_state.session_utilisateur = resultat
                    st.rerun()
                else:
                    st.error(resultat)

            with st.expander("Mot de passe oublié ?"):
                email_oublie = st.text_input("Ton email", key="email_mdp_oublie")
                if st.button("Envoyer le lien de réinitialisation", key="btn_mdp_oublie"):
                    _url_base_retour_oubli = get_secret("URL_RETOUR_APP")
                    _redirection_oubli = (
                        f"{_url_base_retour_oubli.rstrip('/')}/?agent={AGENT_ID}"
                        if _url_base_retour_oubli else None
                    )
                    _, message = demarrer_reinitialisation_mot_de_passe(email_oublie, redirection=_redirection_oubli)
                    st.info(message)

        with onglet_inscription:
            email_inscription = st.text_input("Email", key="email_inscription")
            mdp_inscription = st.text_input("Mot de passe", type="password", key="mdp_inscription")
            if st.button("Créer mon compte", key="bouton_inscription"):
                # Redirection après clic sur le lien de confirmation reçu par
                # email : CET agent précis (pas la plateforme), sinon
                # l'étudiant confirmerait son compte et se retrouverait sur
                # le tableau de bord créateur au lieu de son chat.
                _url_base_retour = get_secret("URL_RETOUR_APP")
                _redirection_inscription = (
                    f"{_url_base_retour.rstrip('/')}/?agent={AGENT_ID}" if _url_base_retour else None
                )
                succes, resultat = inscription(email_inscription, mdp_inscription, redirection=_redirection_inscription)
                if succes:
                    if hasattr(resultat, "user"):
                        # Session directement valide (confirmation email
                        # désactivée sur ce projet) : on connecte tout de
                        # suite, pas besoin de repasser par l'onglet
                        # "Se connecter" juste après avoir créé le compte.
                        st.session_state.session_utilisateur = resultat
                        st.rerun()
                    else:
                        st.success(resultat)
                else:
                    st.error(resultat)
    else:
        email_connecte = st.session_state.session_utilisateur.user.email
        user_id_connecte = st.session_state.session_utilisateur.user.id
        st.markdown(f"Connecté : **{email_connecte}**")

        if "notion_message" in st.session_state:
            st.info(st.session_state.pop("notion_message"))

        st.markdown("---")
        if est_connecte(user_id_connecte):
            st.caption("📓 Notion connecté (compte, valable pour tous les agents)")
        else:
            # Compte unifié : la connexion Notion, une fois établie
            # depuis n'importe quel agent, vaut pour tous les autres.
            st.caption("📓 Notion non connecté")
            if st.button("Connecter mon Notion"):
                url = demarrer_connexion_notion(user_id_connecte, AGENT_ID)
                if url:
                    st.link_button("Continuer vers Notion", url)
                else:
                    st.error("Connexion Notion impossible pour le moment.")

        st.markdown("---")
        if st.button("Se déconnecter"):
            deconnexion()
            st.session_state.session_utilisateur = None
            st.rerun()


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


if "messages" not in st.session_state:
    st.session_state.messages = []

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
    if colonne_confirmer.button("✅ Confirmer", key="confirmer_outil"):
        decision = True
    if colonne_annuler.button("❌ Annuler", key="annuler_outil"):
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
    _url_retour_inscription = get_secret("URL_RETOUR_APP")
    # "/inscription" est l'URL cible une fois le frontend Next.js de la
    # plateforme en place (voir PIVOT_SOCIAL.md) ; en attendant, si le
    # secret est absent, on n'affiche simplement pas de bouton plutôt que
    # de pointer vers un lien cassé.
    _lien_inscription = (
        f"{_url_retour_inscription.rstrip('/')}/inscription"
        if _url_retour_inscription else None
    )
    st.info(
        "Tu as atteint la limite de messages en tant que visiteur non "
        "connecté. Crée un compte gratuitement pour continuer à discuter "
        "avec cet agent."
    )
    if _lien_inscription:
        st.link_button("Créer mon compte", _lien_inscription)
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
        generateur = chat(prompt, historique, user_id_courant, agent_id=AGENT_ID)
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
# Conditionné à UI_CONFIG["rendu_visuel"] (point 5, Interface) : inutile
# de charger MathJax pour un agent qui ne manipule jamais de formules.
if UI_CONFIG["rendu_visuel"]:
    _typeset_mathjax()

if st.session_state.compteur >= 3:
    st.markdown("---")
    st.markdown("Ton avis compte, dis-nous ce que tu penses !")
    st.link_button("Remplir le formulaire", "https://forms.gle/zQPQsb9cX46188oh9")
