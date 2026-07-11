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
from themes import police_vers_css, RAYONS, RAYON_PAR_DEFAUT, TAILLES, TAILLE_PAR_DEFAUT
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
    "couleur_fond": "rgba(100, 100, 100, 0.2)",
    "couleur_accent": "#8B5E3C",
    # Nouveaux champs de thème (contrôle visuel complet, pas juste un
    # accent isolé). Défauts choisis pour ne RIEN changer visuellement
    # aux agents déjà créés qui n'ont pas ces clés dans leur ui_config :
    # "transparent" = pas de bulle assistant (comportement historique),
    # la bordure grise reprend exactement l'ancienne valeur codée en dur.
    "couleur_bulle_assistant": "transparent",
    "couleur_bordure": "rgba(128, 128, 128, 0.3)",
    "police": "Lora (serif, actuelle)",
    "css_avance": "",
    # --- Contrôle visuel étendu (maximum contrôlable par formulaire) ---
    # Défauts choisis pour être EXACTEMENT le rendu actuel quand la clé est
    # absente : aucun agent existant ne doit changer d'apparence tant que
    # son créateur n'a pas explicitement choisi une nouvelle valeur.
    "couleur_fond_page": "",  # "" = pas de surcharge (comportement Streamlit normal, clair/sombre auto)
    "couleur_texte_utilisateur": "inherit",  # s'adapte au mode clair/sombre du visiteur
    "couleur_texte_assistant": "inherit",
    "couleur_texte_bouton": "#FFFFFF",  # texte des boutons (à choisir contrasté avec couleur_accent)
    "couleur_lien": "",  # "" = utilise couleur_accent (comportement actuel, un seul réglage pour les deux)
    "couleur_bouton_fond": "",  # "" = utilise couleur_accent
    "rayon_bulles": "18px",
    "taille_texte": "",  # "" = taille par défaut de Streamlit, pas de surcharge
    # Chantier thème (bulle assistant + titre logo multicolore). Défauts
    # choisis pour NE RIEN changer visuellement aux agents existants sans
    # ces clés : bulle_assistant_visible=True -> couleur_bulle_assistant
    # s'applique telle quelle (déjà "transparent" par défaut plus haut,
    # donc identique à avant). titre_couleur_unique="#000000" et
    # titre_couleurs_lettres=None -> st.title() classique, pas de <span>
    # custom (voir rendu plus bas).
    "bulle_assistant_visible": True,
    "titre_couleur_unique": "#000000",
    "titre_couleurs_lettres": None,
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

# Police : import Google Fonts (si besoin) + pile CSS finale, résolus via
# le module partagé core/themes.py (voir sa docstring pour les alias de
# compatibilité ascendante).
_IMPORT_POLICE, _POLICE_CSS = police_vers_css(UI_CONFIG["police"])

# Rayon des bulles et taille du texte : mêmes libellés que dans le
# formulaire (creer_agent.py/mes_agents.py) -> valeur CSS réelle. Si la
# valeur stockée ne correspond à aucun libellé connu (champ jamais
# rempli, ou ancien format "18px" déjà en px directement), on l'utilise
# telle quelle : ça permet aussi bien un ancien agent qu'un futur réglage
# fin non exposé dans le formulaire.
_RAYON_CSS = RAYONS.get(UI_CONFIG["rayon_bulles"], UI_CONFIG["rayon_bulles"] or "18px")
_TAILLE_CSS = TAILLES.get(UI_CONFIG["taille_texte"], UI_CONFIG["taille_texte"])

# couleur_lien / couleur_bouton_fond permettent de distinguer liens et
# boutons ; "" (réglage non touché par le créateur) retombe sur
# couleur_accent, le comportement historique (un seul réglage pour les deux).
_COULEUR_LIEN = UI_CONFIG["couleur_lien"] or UI_CONFIG["couleur_accent"]
_COULEUR_BOUTON_FOND = UI_CONFIG["couleur_bouton_fond"] or UI_CONFIG["couleur_accent"]

# Chantier thème, point 1 (toggle bulle assistant) : si le créateur a
# décoché "Afficher les réponses dans une bulle visible", on force
# transparent quoi que contienne couleur_bulle_assistant en base (au cas
# où une valeur opaque y traînerait d'un réglage précédent), et on retire
# le padding horizontal + arrondi qui donnent un effet "boîte" même sur
# fond transparent (visible via l'ombre/la sélection de texte sinon).
_BULLE_VISIBLE = UI_CONFIG.get("bulle_assistant_visible", True)
if _BULLE_VISIBLE:
    _COULEUR_BULLE_ASSISTANT = UI_CONFIG["couleur_bulle_assistant"]
    _PADDING_BULLE_ASSISTANT = "10px 4px"
    _RAYON_BULLE_ASSISTANT = _RAYON_CSS
else:
    _COULEUR_BULLE_ASSISTANT = "transparent"
    _PADDING_BULLE_ASSISTANT = "10px 0"
    _RAYON_BULLE_ASSISTANT = "0px"

st.markdown(f"""
    <style>
    {_IMPORT_POLICE}

    /* "" (valeur non définie) produit une déclaration CSS invalide, que
       le navigateur ignore silencieusement -> pas de surcharge, comme
       souhaité, sans if/else Python séparé pour chaque propriété. */
    .stApp {{
        background-color: {UI_CONFIG["couleur_fond_page"]};
    }}

    /* En-tête natif Streamlit (barre "Share / étoile / crayon / GitHub / ⋮")
       et pied de page ("Made with Streamlit") : supprimés visuellement et
       sans espace réservé.
       ATTENTION (bug du dernier essai, symptôme : "la sidebar n'y est plus
       du tout") : header et [data-testid="stHeader"] contiennent AUSSI le
       bouton d'ouverture de la sidebar quand elle est repliée -> les mettre
       en display:none (comme avant) supprime ce bouton avec le reste, sans
       recours possible pour un enfant (display:none n'est jamais réversible
       par un descendant). On utilise donc visibility:hidden sur header lui
       même (efface tout son contenu par défaut), qui elle EST réversible :
       juste après, on force visibility:visible sur le bouton toggle
       précisément -> il redevient seul visible dans un header par ailleurs
       invisible et sans hauteur. #MainMenu/toolbar/decoration/status n'ont
       pas ce problème (rien d'utile ne vit dedans) -> display:none reste
       approprié pour eux. */
    #MainMenu,
    footer,
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stStatusWidget"] {{
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }}
    header,
    [data-testid="stHeader"] {{
        visibility: hidden !important;
        height: 0 !important;
        min-height: 0 !important;
        background: transparent !important;
    }}
    /* Sans en-tête, block-container reprend tout l'espace du haut : on
       enlève le padding réservé pour lui, sinon un vide persiste en haut
       de page même une fois l'en-tête retiré. */
    .block-container {{
        padding-top: 2rem !important;
    }}

    /* Barre latérale (st.sidebar) : jusqu'ici jamais stylée -> elle gardait
       le gris clair par défaut de Streamlit, qui jurait avec couleur_fond_page
       sur les agents à thème sombre. On l'aligne explicitement, sur les deux
       niveaux (conteneur + zone de contenu) pour parer aux versions de
       Streamlit où le fond est peint par l'un ou l'autre. */
    [data-testid="stSidebar"],
    [data-testid="stSidebarContent"],
    [data-testid="stSidebarUserContent"] {{
        background: {UI_CONFIG["couleur_fond_page"]} !important;
        background-color: {UI_CONFIG["couleur_fond_page"]} !important;
    }}
    [data-testid="stSidebar"] {{
        border-right: none !important;
        box-shadow: none !important;
    }}

    /* Bouton d'affichage/masquage de la sidebar (icône "<<"/">>") : Streamlit
       lui donne une couleur d'icône fixe pensée pour son thème par défaut,
       PAS pour couleur_fond_page choisie par le créateur -> sur un fond
       sombre/noir, l'icône devient invisible (noir sur noir). On lui donne
       un badge de fond neutre semi-opaque + une icône blanche forcée, qui
       restent lisibles quel que soit couleur_fond_page (clair ou sombre).
       visibility:visible + position:fixed forcés : ce bouton peut vivre
       dans le header qu'on vient de rendre invisible/sans hauteur juste
       au-dessus -> sans ça, il resterait cascadé invisible avec son parent
       (cas du bug "sidebar disparue") ou écrasé par la hauteur 0 du header.
       Volontairement PAS de display:none ici : cacher ce conteneur casserait
       le bouton lui-même (plus moyen de rouvrir la sidebar une fois repliée),
       ce n'est qu'un habillage visuel. */
    [data-testid="stSidebarCollapsedControl"],
    [data-testid="stSidebarCollapseButton"],
    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapsedControl"] button,
    [data-testid="stSidebarCollapseButton"] button {{
        visibility: visible !important;
        position: fixed !important;
        top: 0.6rem !important;
        left: 0.6rem !important;
        z-index: 999999 !important;
        background-color: rgba(120, 120, 120, 0.35) !important;
        border-radius: 6px !important;
    }}
    [data-testid="stSidebarCollapsedControl"] svg,
    [data-testid="stSidebarCollapseButton"] svg,
    [data-testid="stExpandSidebarButton"] svg {{
        visibility: visible !important;
        fill: #FFFFFF !important;
        color: #FFFFFF !important;
    }}

    /* Barre de saisie (st.chat_input) : par défaut, Streamlit la loge dans
       une bande pleine largeur avec SON PROPRE fond clair, indépendant de
       couleur_fond_page -> c'est ce bandeau clair qui jurait avec le fond
       sombre choisi. On fait disparaître cette bande (même couleur que la
       page -> invisible) et on donne à l'input lui-même une allure de
       pilule flottante, centrée, qui reprend automatiquement la couleur de
       fond choisie -> plus aucun réglage manuel si le créateur change sa
       couleur de fond, ça suit tout seul.
       Note : le nom exact de ce conteneur a changé entre versions de
       Streamlit (stBottomBlockContainer / stChatFloatingInputContainer) ->
       tous les niveaux connus sont ciblés pour rester robuste. Piège du
       dernier essai : [data-testid="stBottomBlockContainer"] seul ne colore
       QUE la colonne centrale (largeur du contenu, pas de la page) -> les
       deux bandes latérales, peintes par le conteneur PARENT plein largeur
       ([data-testid="stBottom"] et son wrapper direct), restaient visibles.
       On colore donc explicitement chaque niveau, du plus englobant (plein
       largeur) au plus interne, avec la même couleur -> plus aucune bande,
       ni au centre ni sur les côtés. Volontairement PAS de display:none ici :
       ça avait fait disparaître le champ de saisie lui-même la dernière fois
       (il n'est pas juste décoratif, il contient l'input fonctionnel). */
    [data-testid="stBottom"],
    [data-testid="stBottom"] > div,
    [data-testid="stBottomBlockContainer"],
    .stChatFloatingInputContainer,
    .stChatInputContainer {{
        background: {UI_CONFIG["couleur_fond_page"]} !important;
        background-color: {UI_CONFIG["couleur_fond_page"]} !important;
        box-shadow: none !important;
        border-top: none !important;
    }}
    /* La pilule de saisie elle-même : contrairement à la bande qui l'entoure
       (transparente/fondue), elle DOIT rester visiblement délimitée (bordure
       + ombre) pour qu'on voie où écrire -> sans ça, sur un fond uni de même
       couleur, l'input devient invisible ("null" à l'écran, signalé la
       dernière fois). C'est le seul endroit où le fond de page sert de
       couleur de remplissage plutôt que de camouflage. */
    /* La pilule de saisie elle-même : le créateur a dit explicitement
       "proche du fond, pas identique" -> une teinte calculée automatiquement
       à partir de couleur_fond_page (pas une valeur fixe), qui reste
       distincte du fond que ce dernier soit clair ou sombre. color-mix()
       pousse la couleur vers un gris neutre à 20% : sur un fond noir ça
       donne un charbon un peu plus clair, sur un fond blanc un gris clair
       un peu plus foncé -> toujours "proche" dans les deux cas, sans
       réglage manuel. Repli : la couleur unie de couleur_fond_page est
       déclarée AVANT color-mix() -> si le navigateur ne comprend pas
       color-mix (rare), il ignore cette ligne et garde la couleur unie
       (fond identique, pas de rupture visuelle) plutôt qu'une erreur. */
    [data-testid="stChatInput"] {{
        background-color: {UI_CONFIG["couleur_fond_page"]} !important;
        background-color: color-mix(in srgb, {UI_CONFIG["couleur_fond_page"]} 80%, gray 20%) !important;
        border: 1px solid {UI_CONFIG["couleur_bordure"]} !important;
        border-radius: 999px !important;
        box-shadow: 0 6px 24px rgba(0, 0, 0, 0.28) !important;
        max-width: 720px;
        margin: 0 auto 1.1rem auto !important;
    }}
    [data-testid="stChatInput"] textarea {{
        background-color: transparent !important;
        color: {UI_CONFIG["couleur_texte_utilisateur"]} !important;
    }}

    .message-user {{
        background-color: {UI_CONFIG["couleur_fond"]};
        color: {UI_CONFIG["couleur_texte_utilisateur"]};
        padding: 12px 18px;
        border-radius: {_RAYON_CSS};
        margin: 8px 0;
        display: inline-block;
        max-width: 75%;
        float: right;
        text-align: right;
        border: 1px solid {UI_CONFIG["couleur_bordure"]};
        font-size: {_TAILLE_CSS};
    }}

    .message-assistant {{
        font-family: {_POLICE_CSS};
        color: {UI_CONFIG["couleur_texte_assistant"]};
        background-color: {_COULEUR_BULLE_ASSISTANT};
        padding: {_PADDING_BULLE_ASSISTANT};
        margin: 8px 0;
        max-width: 85%;
        line-height: 1.7;
        border-radius: {_RAYON_BULLE_ASSISTANT};
        font-size: {_TAILLE_CSS};
    }}

    .clearfix {{ clear: both; }}

    .statut-outil {{
        font-family: {_POLICE_CSS};
        font-style: italic;
        font-size: 0.85em;
        color: rgba(128, 128, 128, 0.9);
        padding: 4px 4px;
        margin: 4px 0 0 0;
    }}

    a {{ color: {_COULEUR_LIEN}; }}

    /* Thème des boutons natifs Streamlit (envoi du chat, Confirmer/Annuler,
       formulaires, liens-boutons). Ciblage par sélecteurs CSS génériques
       (pas une API Streamlit officielle) : ça fonctionne sur les versions
       actuelles, mais une future version de Streamlit qui renommerait ses
       classes internes casserait ce theming (pas le reste de l'app, juste
       ces couleurs). */
    .stButton button, .stFormSubmitButton button, .stLinkButton a,
    div[data-testid="stChatInput"] button {{
        background-color: {_COULEUR_BOUTON_FOND} !important;
        border-color: {_COULEUR_BOUTON_FOND} !important;
        color: {UI_CONFIG["couleur_texte_bouton"]} !important;
    }}

    /* CSS avancé du créateur (faces/vues/creer_agent.py, section 5 -
       "réservé aux personnes à l'aise en CSS"). Placé en dernier pour
       pouvoir surcharger les règles ci-dessus si besoin. */
    {UI_CONFIG["css_avance"]}
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
        if est_connecte(user_id_connecte, AGENT_ID):
            st.caption("📓 Notion connecté (pour cet agent)")
        else:
            # Option A (scoping strict) : même si cet étudiant a déjà
            # connecté Notion pour un AUTRE agent, ça ne compte pas ici —
            # message volontairement explicite pour ne pas laisser croire
            # à un bug si l'étudiant se souvient s'être déjà connecté
            # ailleurs sur la plateforme.
            st.caption("📓 Notion non connecté pour cet agent")
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


def _rendre_titre_accueil(ui_config):
    """
    Chantier thème, point 2 : remplace st.title() par un rendu custom dès
    qu'une personnalisation de couleur existe, pour permettre le mode
    multicolore (une couleur par caractère, effet "logo"). Retombe sur
    l'équivalent visuel de st.title() (taille/graisse similaires) si
    aucune personnalisation n'est enregistrée, pour ne rien changer aux
    agents existants sans ces clés.

    Priorité : titre_couleurs_lettres (liste) si rempli et de la même
    longueur que le texte -> mode multicolore. Sinon titre_couleur_unique
    si différent de "#000000" (valeur "pas de surcharge"). Sinon rendu
    par défaut.
    """
    texte = ui_config["titre_accueil"]
    couleurs_lettres = ui_config.get("titre_couleurs_lettres")
    couleur_unique = ui_config.get("titre_couleur_unique", "#000000")

    style_titre_base = "font-size: 2.25rem; font-weight: 700; line-height: 1.2; margin: 0.5rem 0 0.25rem 0;"

    if couleurs_lettres and len(couleurs_lettres) == len(texte):
        # Longueur non concordante (ex: titre modifié en base sans
        # régénérer les couleurs) -> on ignore le mode multicolore plutôt
        # que de mal aligner lettres et couleurs ; voir branches suivantes.
        spans = "".join(
            f'<span style="color: {couleur};">{caractere}</span>'
            for caractere, couleur in zip(texte, couleurs_lettres)
        )
        st.markdown(f'<div style="{style_titre_base}">{spans}</div>', unsafe_allow_html=True)
    elif couleur_unique and couleur_unique.lower() != "#000000":
        st.markdown(
            f'<div style="{style_titre_base} color: {couleur_unique};">{texte}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.title(texte)


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


if prompt := st.chat_input(UI_CONFIG["placeholder_saisie"]):
    st.session_state.compteur += 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.markdown(f'<div class="message-user">{prompt}</div><div class="clearfix"></div>', unsafe_allow_html=True)

    historique = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]

    placeholder_statut = st.empty()
    placeholder = st.empty()

    user_id_courant = (
        st.session_state.session_utilisateur.user.id
        if st.session_state.session_utilisateur else None
    )

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
