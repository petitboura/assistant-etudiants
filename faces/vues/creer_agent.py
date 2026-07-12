"""
Face créateur — formulaire de création d'agent (plateforme djiguigne).

ÉTAPE 2 du plan multi-agent : remplace la création manuelle (Boumi qui
ajoute une ligne dans Supabase à la main) par une vraie interface.

Ce que ce formulaire fait :
- Écrit une nouvelle ligne dans la table `agents` (id, nom, system_prompt,
  ui_config, tools_enabled, owner_id).
- Le créateur doit être connecté (réutilise core/auth.py, déjà en place)
  pour que owner_id soit renseigné -> voir Étape 3 du plan ("mes agents").
- N'écrit JAMAIS dans Notion : contrairement aux agents historiques
  (tutorat-maths, telecom-ia), les agents créés ici stockent leur prompt
  directement dans la colonne `agents.system_prompt` (voir migration
  add_system_prompt_column_to_agents). L'utilisateur de la plateforme n'a
  pas besoin d'un compte Notion.

Ce que ce formulaire NE fait PAS encore (volontairement, hors scope de
l'étape 2) :
- Upload de documents pour le RAG -> Étape 4 du plan.
- Sous-domaine automatique / URL propre -> Étape 5 du plan. Pour l'instant
  l'agent créé est accessible via ?agent=<id> sur le déploiement existant
  (voir faces/app_etudiant.py, résolution dynamique déjà en place).
"""

import os
import re
import sys
import logging

import streamlit as st
from supabase import create_client

# Nécessaire pour importer core/auth.py et indexers/index_documents.py,
# indexers/storage.py depuis faces/ (absent jusqu'ici dans ce fichier —
# fonctionnait seulement si le process de déploiement ajoutait déjà core/
# au PYTHONPATH par un autre biais ; on aligne ici sur le même mécanisme
# explicite que faces/app_etudiant.py, pour ne plus en dépendre).
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'indexers'))
# Le dossier vues/ lui-même : st.navigation() exécute cette page via exec(),
# ce qui n'ajoute PAS automatiquement son propre dossier à sys.path
# (contrairement à un lancement direct `python vues/creer_agent.py`) ->
# sans cette ligne, l'import de theme_djiguigne.py juste à côté échoue
# avec ModuleNotFoundError.
sys.path.append(os.path.dirname(__file__))

from auth import inscription, connexion, deconnexion, demarrer_reinitialisation_mot_de_passe  # noqa: E402
from index_documents import indexer_document, indexer_texte  # noqa: E402
from storage import upload_document  # noqa: E402
from themes import POLICES_AFFICHEES, POLICE_PAR_DEFAUT, RAYONS, RAYON_PAR_DEFAUT, TAILLES, TAILLE_PAR_DEFAUT  # noqa: E402
from creation_agent import generer_id_depuis_nom, extraire_id_notion, composer_system_prompt  # noqa: E402
from recuperation_mdp import gerer_recuperation_mot_de_passe  # noqa: E402

# Identité visuelle Djiguignè AI (voir vues/theme_djiguigne.py) : ce module
# ne restyle QUE l'habillage (couleurs, polices, header) et ne touche à
# aucune logique métier ci-dessous.
from theme_djiguigne import injecter_theme, afficher_entete  # noqa: E402

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

# Liste des outils proposables au créateur = exactement ce que le moteur
# MCP sait déjà connecter (core/registre_outils.py). On importe le
# registre plutôt que de recopier les noms en dur, pour que cette liste
# reste toujours synchronisée avec ce qui existe réellement -> si un
# outil est ajouté/retiré dans registre_outils.py, ce formulaire suit
# automatiquement, sans modification ici.
try:
    from registre_outils import SERVEURS_MCP
    OUTILS_DISPONIBLES = [s["nom"] for s in SERVEURS_MCP]
except Exception as e:
    logging.error(f"ERREUR import registre_outils : {e}")
    OUTILS_DISPONIBLES = []

DESCRIPTIONS_OUTILS = {
    "wolfram": "Calculs et résolution mathématique (Wolfram Alpha)",
    "tavily": "Recherche web en temps réel",
    "notion": "Lecture du Notion personnel de l'utilisateur final (nécessite qu'il connecte son compte)",
}

st.set_page_config(
    page_title="Créer un agent — Djiguignè AI",
    page_icon="🧩",
    layout="centered",
)
injecter_theme()


# --- Connexion obligatoire --------------------------------------------
# Réutilise core/auth.py tel quel (déjà en place, email/mdp + Google).
# Un agent créé sans utilisateur connecté n'aurait pas de owner_id, donc
# personne ne pourrait le retrouver dans "mes agents" plus tard (Étape 3).
if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

if not st.session_state.session_utilisateur:
    if gerer_recuperation_mot_de_passe():
        st.stop()

    afficher_entete()
    st.markdown(
        """
        <div style="text-align:center; margin: 0.5rem 0 1.8rem 0; animation: dj-fade-up 0.5s ease both;">
            <h1 class="dj-display" style="font-size:2.1rem; margin-bottom:0.3rem;">Crée ton agent IA</h1>
            <p style="color:var(--dj-texte-muet); font-size:1.02rem; max-width:480px; margin:0 auto;">
                Configure ton assistant, il est en ligne immédiatement — aucun
                déploiement de ton côté.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    onglet_connexion, onglet_inscription = st.tabs(["Se connecter", "Créer un compte"])

    with onglet_connexion:
        email = st.text_input("Email", key="email_connexion")
        mot_de_passe = st.text_input("Mot de passe", type="password", key="mdp_connexion")
        if st.button("Se connecter", key="btn_connexion", use_container_width=True):
            succes, resultat = connexion(email, mot_de_passe)
            if succes:
                st.session_state.session_utilisateur = resultat
                # Un compte existant a presque toujours deja au moins un
                # agent : on va directement au tableau de bord plutot que
                # de montrer ce formulaire de creation. "Creer un autre
                # agent" reste accessible depuis "Mes agents".
                st.switch_page("vues/mes_agents.py")
            else:
                st.error(resultat)

        with st.expander("Mot de passe oublié ?"):
            email_oublie = st.text_input("Ton email", key="email_mdp_oublie")
            if st.button("Envoyer le lien de réinitialisation", key="btn_mdp_oublie"):
                _, message = demarrer_reinitialisation_mot_de_passe(
                    email_oublie,
                    # "?ctx=..." garantit qu'il y a toujours un paramètre
                    # existant dans l'URL -> le template email (Supabase)
                    # peut accoler "&token_hash=..." sans produire une URL
                    # invalide à deux "?" (voir core/auth.py).
                    redirection=f"{get_secret('URL_RETOUR_APP').rstrip('/')}/?ctx=plateforme"
                    if get_secret("URL_RETOUR_APP") else None,
                )
                st.info(message)

    with onglet_inscription:
        email_new = st.text_input("Email", key="email_inscription")
        mdp_new = st.text_input("Mot de passe", type="password", key="mdp_inscription")
        if st.button("Créer mon compte", key="btn_inscription", use_container_width=True):
            # Redirection après clic sur le lien de confirmation reçu par
            # email : la plateforme (contexte créateur), pas un agent précis
            # -> distingue ce cas de l'inscription "étudiant" dans chat.py.
            succes, resultat = inscription(email_new, mdp_new, redirection=get_secret("URL_RETOUR_APP"))
            if succes:
                if hasattr(resultat, "user"):
                    # Session directement valide (confirmation email
                    # désactivée sur ce projet) : nouveau compte, donc pas
                    # encore d'agent -> on reste ICI pour créer le premier,
                    # contrairement à "Se connecter" qui redirige vers le
                    # tableau de bord.
                    st.session_state.session_utilisateur = resultat
                    st.rerun()
                else:
                    st.success(resultat)
            else:
                st.error(resultat)

    st.stop()


# --- Utilisateur connecté : formulaire de création ---------------------
user_id = st.session_state.session_utilisateur.user.id
email_utilisateur = st.session_state.session_utilisateur.user.email


def _deconnexion_creer_agent():
    deconnexion()
    st.session_state.session_utilisateur = None
    st.rerun()


afficher_entete(email_utilisateur=email_utilisateur, sur_clic_deconnexion=_deconnexion_creer_agent)
st.markdown(
    """
    <h1 class="dj-display" style="font-size:1.7rem;">🧩 Nouvel agent</h1>
    """,
    unsafe_allow_html=True,
)

# Nombre de lignes proposées pour le point 2 (comportement situationnel).
# Fixe et non dynamique car un st.form Streamlit ne peut pas ajouter de
# champs à la volée sans se soumettre — 4 couvre largement un premier
# jet (ex: "Exemple / Démonstration / Cours / Exercice" pour un tuteur).
# Les lignes laissées vides sont simplement ignorées à la composition.
NB_LIGNES_COMPORTEMENT = 4

# Hors du st.form (comme documenté ci-dessus pour NB_LIGNES_COMPORTEMENT) :
# ce choix doit être réactif immédiatement pour cacher/afficher le color
# picker "Fond — bulle assistant" juste en dessous, ce qu'un widget DANS
# un st.form ne peut pas faire (aucun rerun tant que le formulaire n'est
# pas soumis). Lu par le formulaire plus bas via cette variable Python
# normale, pas via st.session_state : suffisant ici car la valeur n'a pas
# besoin de survivre à un rerun déclenché par autre chose que ce widget
# lui-même.
st.divider()
st.subheader("🎨 Style visuel — aperçu en direct")
st.caption(
    "Ces réglages sont en dehors du formulaire principal pour réagir immédiatement "
    "(le reste des couleurs — fonds, textes, police — se trouve plus bas, dans la "
    "section \"Thème visuel\" du formulaire)."
)

bulle_assistant_visible = st.checkbox(
    "Afficher les réponses dans une bulle visible",
    value=True,
    help=(
        "Décoche pour un style texte libre, sans encadré autour des réponses de "
        "l'agent (comme ChatGPT/Claude) — plus moderne et épuré. Coché = les "
        "réponses apparaissent dans une bulle colorée (style Ooredoo, plus "
        "\"chat classique\")."
    ),
)

nom_agent = st.text_input(
    "Nom de l'agent",
    placeholder="Ex: Coach fitness, Support client boutique...",
    help="Affiché aux utilisateurs finaux, dans l'onglet du navigateur et sur l'écran d'accueil.",
)

icone_agent = st.text_input(
    "Icône (emoji)",
    value="🤖",
    max_chars=2,
    help="Un seul emoji, affiché dans l'onglet du navigateur et à côté du titre d'accueil.",
)

# --- Style du titre d'accueil (lettre par lettre ou couleur unique) ----
# Hors du st.form, comme bulle_assistant_visible plus haut : le nombre de
# color pickers générés dépend de la longueur du titre (icône + nom), qui
# doit donc être déjà connue et à jour à ce stade -> nom_agent/icone_agent
# sont sortis du formulaire pour la même raison de réactivité.
_titre_complet_apercu = f"{icone_agent.strip()} {nom_agent.strip()}".strip()

st.markdown("**Style du titre d'accueil**")
style_titre = st.radio(
    "Style du titre d'accueil",
    ["Couleur unique", "Multicolore (chaque lettre)"],
    horizontal=True,
    label_visibility="collapsed",
    help=(
        "Multicolore : effet \"logo\", chaque caractère du titre a sa propre couleur "
        "(ex: chaque lettre d'un nom de marque dans une couleur différente)."
    ),
)

if style_titre == "Multicolore (chaque lettre)":
    if not _titre_complet_apercu:
        st.caption("Remplis le nom de l'agent ci-dessus pour choisir une couleur par lettre.")
        titre_couleurs_lettres = None
    else:
        st.caption(
            f"Une couleur par caractère de \"{_titre_complet_apercu}\" "
            f"({len(_titre_complet_apercu)} pickers) :"
        )
        # Une couleur par CARACTÈRE, espaces inclus dans le comptage pour
        # que l'alignement caractère<->couleur reste simple et exact au
        # rendu (voir chat.py), même si un espace n'a visuellement pas de
        # couleur propre. Régénéré à chaque frappe car nom_agent/icone_agent
        # sont hors du form (rerun immédiat) -> jamais désynchronisé du
        # texte réellement affiché, contrairement à un form classique.
        palette_defaut = [
            "#E63946", "#F1A208", "#2A9D8F", "#264653", "#8338EC",
            "#FF006E", "#3A86FF", "#06D6A0", "#EF476F", "#FFD166",
        ]
        colonnes_lettres = st.columns(min(len(_titre_complet_apercu), 10))
        titre_couleurs_lettres = []
        for i, caractere in enumerate(_titre_complet_apercu):
            with colonnes_lettres[i % len(colonnes_lettres)]:
                couleur_lettre = st.color_picker(
                    caractere if caractere.strip() else "␣",
                    palette_defaut[i % len(palette_defaut)],
                    key=f"titre_lettre_{i}",
                    label_visibility="visible",
                )
            titre_couleurs_lettres.append(couleur_lettre)
else:
    titre_couleur_unique = st.color_picker(
        "Couleur du titre",
        "#000000",
        help="Une seule couleur pour tout le titre d'accueil. \"#000000\" = couleur par défaut (pas de surcharge).",
    )
    titre_couleurs_lettres = None

st.divider()

with st.form("formulaire_creation_agent"):
    st.subheader("1. Identité de base")
    st.caption(
        "Ce qui reste stable quel que soit le type d'interaction : le ton, "
        "les limites, la posture générale de l'agent."
    )

    ton = st.selectbox(
        "Ton",
        ["Tutoiement (tu)", "Vouvoiement (vous)"],
        help=(
            "Comment l'agent s'adresse à l'utilisateur. Choisis en fonction du public : "
            "le tutoiement rapproche (ex: coaching, tutorat pour étudiants), le vouvoiement "
            "est plus adapté à un contexte professionnel ou à un public large (ex: support client)."
        ),
    )

    posture_generale = st.text_input(
        "Posture générale",
        placeholder="Ex: patient et pédagogue / efficace et factuel / chaleureux et rassurant",
        help=(
            "L'attitude générale de l'agent, en quelques mots. Ça influence tout le style de "
            "réponse, indépendamment du sujet précis de chaque message."
        ),
    )

    limites_globales = st.text_area(
        "Limites globales — ce que l'agent ne fait JAMAIS",
        placeholder=(
            "Ex: ne donne jamais de conseil médical ou juridique, ne partage jamais "
            "d'informations sur un autre utilisateur, refuse de discuter de sujets hors "
            "de son domaine."
        ),
        height=80,
        help=(
            "Des interdits qui s'appliquent quel que soit le contexte de la conversation, "
            "contrairement au comportement du point 2 qui, lui, change selon le type de "
            "requête."
        ),
    )

    sous_titre = st.text_area(
        "Phrase d'accueil",
        placeholder="Ex: Je t'aide à structurer ton entraînement de la semaine.",
        height=70,
        help="Affichée sous le titre, au tout premier écran, avant le premier message de l'utilisateur.",
    )

    placeholder_saisie = st.text_input(
        "Texte du champ de saisie",
        value="Pose ta question...",
        help="Le texte grisé affiché dans la zone de saisie tant que l'utilisateur n'a rien tapé.",
    )

    st.subheader("2. Comportement selon le type de requête")
    st.caption(
        "Un agent qui traite toutes les demandes de la même façon perd en qualité. "
        "Précise, pour chaque grand type de requête que reçoit ton agent, comment il "
        "doit réagir. Ex. pour un tuteur de maths : \"Exercice\" → \"Laisser chercher "
        "avant de donner des indices\". Laisse une ligne vide si tu en as moins de "
        f"{NB_LIGNES_COMPORTEMENT}."
    )

    lignes_comportement = []
    for i in range(NB_LIGNES_COMPORTEMENT):
        col_type, col_comportement = st.columns(2)
        with col_type:
            type_requete = st.text_input(
                "Type de requête",
                key=f"type_requete_{i}",
                placeholder="Ex: Exercice, Réclamation, Cours...",
                label_visibility="visible" if i == 0 else "collapsed",
            )
        with col_comportement:
            comportement_attendu = st.text_input(
                "Comportement attendu",
                key=f"comportement_{i}",
                placeholder="Ex: Laisser chercher avant de donner des indices",
                label_visibility="visible" if i == 0 else "collapsed",
            )
        lignes_comportement.append((type_requete, comportement_attendu))

    st.subheader("3. Capacités (outils disponibles)")
    st.caption(
        "Outils génériques, accessibles à tous les utilisateurs sans configuration "
        "individuelle. Les outils liés à un compte personnel (ex: Notion) demandent à "
        "l'utilisateur final de connecter son propre compte."
    )
    if OUTILS_DISPONIBLES:
        outils_choisis = []
        for nom_outil in OUTILS_DISPONIBLES:
            description = DESCRIPTIONS_OUTILS.get(nom_outil, "")
            if st.checkbox(f"**{nom_outil}** — {description}", key=f"outil_{nom_outil}"):
                outils_choisis.append(nom_outil)
    else:
        outils_choisis = []
        st.warning("Aucun outil disponible pour le moment (registre_outils.py injoignable).")

    st.subheader("4. Base de connaissance")
    st.caption(
        "La nature de la connaissance change selon le domaine, et influence la façon "
        "dont l'agent doit s'en servir — comme une liste de faits figés, ou comme des "
        "exemples/méthodes à adapter."
    )

    type_connaissance = st.radio(
        "Nature de la connaissance",
        [
            "Factuelle et stable (ex: grille tarifaire, procédures — la fraîcheur et l'exactitude priment)",
            "Méthodologique et pédagogique (ex: façon d'expliquer, exemples — sert de méthode plutôt que de faits figés)",
        ],
        help=(
            "Une erreur sur une connaissance factuelle a un coût réel immédiat (ex: mauvais "
            "tarif annoncé) ; une connaissance méthodologique est plus tolérante à "
            "l'approximation, elle guide un raisonnement plutôt qu'elle ne donne un chiffre."
        ),
    )

    description_connaissance = st.text_area(
        "En une phrase, quel type d'information l'agent doit-il connaître ?",
        placeholder="Ex: les forfaits et procédures de résiliation Ooredoo / les méthodes de résolution du programme MPSI",
        height=70,
        help="Utilisé pour orienter le comportement général de l'agent (ajouté au system prompt).",
    )

    st.markdown("**Sources de connaissance (RAG)** — cumulables, toutes optionnelles")
    st.caption(
        "Chaque source est indexée séparément et vient nourrir les réponses de l'agent "
        "par recherche sémantique. Rien n'est obligatoire ici."
    )

    fichier_pdf = st.file_uploader(
        "Document PDF",
        type=["pdf"],
        help="Le contenu est extrait et indexé automatiquement à la création de l'agent.",
    )

    lien_notion = st.text_input(
        "Lien ou ID d'une page Notion",
        placeholder="https://www.notion.so/... ou directement l'ID de la page",
        help=(
            "La page (et ses sous-pages) sera indexée automatiquement par la synchronisation "
            "périodique existante — pas besoin de compte Notion partagé avec la plateforme, "
            "juste que la page soit partagée avec l'intégration Notion du projet."
        ),
    )

    texte_libre = st.text_area(
        "Texte de connaissance libre",
        placeholder=(
            "Écris ici directement ce que l'agent doit savoir — utile pour une connaissance "
            "courte qui ne mérite pas tout un PDF ou une page Notion."
        ),
        height=150,
        help="Modifiable à tout moment depuis \"Mes agents\" ; chaque sauvegarde réindexe ce texte.",
    )

    st.subheader("5. Interface")
    st.caption(
        "Le même agent (même prompt, mêmes outils) se comporte différemment selon "
        "comment on interagit avec lui."
    )

    raisonnement_visible = st.checkbox(
        "Afficher le raisonnement de l'agent, pas juste sa réponse finale",
        value=False,
        help=(
            "Utile pour un agent pédagogique où voir le cheminement compte autant que la "
            "réponse ; à laisser désactivé pour un agent orienté efficacité (ex: support "
            "client, réponse courte et actionnable)."
        ),
    )

    rendu_visuel = st.checkbox(
        "Activer le rendu visuel des formules mathématiques (LaTeX)",
        value=False,
        help="À activer seulement si l'agent va manipuler des formules mathématiques (ex: tutorat).",
    )

    memoire_visible = st.checkbox(
        "L'utilisateur final peut voir/modifier l'historique de la conversation",
        value=True,
        help="Décoche si tu préfères une mémoire invisible, gérée uniquement en interne par l'agent.",
    )

    st.markdown("**Thème visuel**")
    st.caption(
        "Contrôle complet de l'apparence de ton agent. Chaque réglage explique son "
        "impact — si tu n'es pas sûr, laisse la valeur par défaut, pensée pour bien "
        "fonctionner ensemble."
    )

    st.markdown("*Couleurs — arrière-plans*")
    st.caption("↑ La visibilité de la bulle assistant se règle tout en haut de la page (\"Style visuel — aperçu en direct\").")
    col_fond_page, col_fond_bulle_user, col_fond_bulle_assistant = st.columns(3)
    with col_fond_page:
        couleur_fond_page = st.color_picker(
            "Fond de la page",
            "#FFFFFF",
            help=(
                "La couleur derrière tout le reste, visible sur les bords de l'écran et "
                "entre les bulles. Un fond légèrement teinté (ex: gris très clair, rose "
                "pâle) donne un look plus habillé qu'un simple blanc — voir l'exemple "
                "Ooredoo (fond gris rosé) vs un agent par défaut (fond blanc)."
            ),
        )
    with col_fond_bulle_user:
        couleur_fond = st.color_picker(
            "Fond — bulle utilisateur",
            "#646464",
            help="La couleur de fond des messages QUE TU ENVOIES (à droite de l'écran).",
        )
    with col_fond_bulle_assistant:
        if bulle_assistant_visible:
            couleur_bulle_assistant = st.color_picker(
                "Fond — bulle assistant",
                "#FFFFFF",
                help=(
                    "La couleur de fond des réponses DE L'AGENT (à gauche). Blanc = "
                    "style \"carte\" bien visible (comme Ooredoo)."
                ),
            )
        else:
            # Case "Afficher les réponses dans une bulle visible" décochée
            # au-dessus du formulaire : le color picker n'a plus de sens
            # (transparent quoi qu'il arrive), donc on ne l'affiche pas —
            # évite de laisser un réglage actif mais sans effet, source de
            # confusion pour un créateur non-technique.
            couleur_bulle_assistant = "transparent"
            st.caption("🫥 Bulle masquée (style texte libre)")

    st.markdown("*Couleurs — texte*")
    col_texte_user, col_texte_assistant, col_texte_bouton = st.columns(3)
    with col_texte_user:
        couleur_texte_utilisateur = st.color_picker(
            "Texte — bulle utilisateur",
            "#FFFFFF",
            help=(
                "Couleur du texte DANS la bulle utilisateur. Doit contraster avec le "
                "\"Fond — bulle utilisateur\" choisi juste au-dessus, sinon illisible."
            ),
        )
    with col_texte_assistant:
        couleur_texte_assistant = st.color_picker(
            "Texte — bulle assistant",
            "#000000",
            help=(
                "Couleur du texte des réponses de l'agent. Doit contraster avec le "
                "\"Fond — bulle assistant\" choisi juste au-dessus."
            ),
        )
    with col_texte_bouton:
        couleur_texte_bouton = st.color_picker(
            "Texte des boutons",
            "#FFFFFF",
            help=(
                "Couleur du texte à l'intérieur des boutons (envoyer, confirmer/annuler, "
                "etc). Doit contraster avec la couleur d'accent choisie ci-dessous — "
                "blanc marche bien sur un accent foncé, noir sur un accent clair."
            ),
        )

    st.markdown("*Couleurs — accent (liens et boutons)*")
    couleur_accent = st.color_picker(
        "Couleur d'accent",
        "#8B5E3C",
        help=(
            "La couleur \"signature\" de ton agent : utilisée par défaut à la fois pour "
            "les liens et pour le fond des boutons (envoyer, confirmer/annuler). Pour la "
            "grande majorité des cas, ce seul réglage suffit."
        ),
    )
    with st.expander("Distinguer la couleur des liens de celle des boutons (optionnel)"):
        st.caption(
            "Laisse vide (couleur par défaut du color picker retirée manuellement, "
            "champ ci-dessous) pour que liens ET boutons utilisent la couleur d'accent "
            "ci-dessus. Ne remplis que si tu veux vraiment deux couleurs différentes."
        )
        distinguer_lien_bouton = st.checkbox(
            "Utiliser une couleur différente pour les liens et pour les boutons",
            value=False,
        )
        couleur_lien = ""
        couleur_bouton_fond = ""
        if distinguer_lien_bouton:
            col_lien, col_bouton = st.columns(2)
            with col_lien:
                couleur_lien = st.color_picker("Couleur des liens", "#8B5E3C")
            with col_bouton:
                couleur_bouton_fond = st.color_picker("Couleur de fond des boutons", "#8B5E3C")

    col_bordure, col_rayon, col_taille = st.columns(3)
    with col_bordure:
        couleur_bordure = st.color_picker(
            "Couleur des bordures",
            "#808080",
            help="Bordure autour de la bulle utilisateur.",
        )
    with col_rayon:
        rayon_bulles = st.selectbox(
            "Arrondi des bulles",
            list(RAYONS.keys()),
            index=list(RAYONS.keys()).index(RAYON_PAR_DEFAUT),
            help="La forme des coins des bulles de message, de carré à très arrondi.",
        )
    with col_taille:
        taille_texte = st.selectbox(
            "Taille du texte",
            list(TAILLES.keys()),
            index=list(TAILLES.keys()).index(TAILLE_PAR_DEFAUT),
            help="La taille du texte des messages (utilisateur et assistant).",
        )

    police = st.selectbox(
        "Police du texte des réponses",
        POLICES_AFFICHEES,
        index=POLICES_AFFICHEES.index(POLICE_PAR_DEFAUT),
        help=(
            "Le style typographique de l'agent. Une police serif (Lora, Merriweather) "
            "évoque le pédagogique/éditorial ; une sans-serif (Poppins, Inter) évoque le "
            "moderne/professionnel ; monospace (Roboto Mono) évoque le technique. Chaque "
            "police (sauf \"Police système\") est chargée depuis Google Fonts, ce qui "
            "ajoute un tout petit délai de chargement au premier affichage."
        ),
    )

    with st.expander("⚙️ Options avancées — réservé aux personnes à l'aise en CSS"):
        st.caption(
            "Ce champ est pour les développeurs. Il permet d'écrire directement du CSS "
            "pour aller au-delà des couleurs/police ci-dessus. Une erreur ici n'affecte "
            "que l'affichage de CET agent, jamais celui des autres agents ni de la "
            "plateforme — corrigeable à tout moment en revenant modifier ce champ."
        )
        css_avance = st.text_area(
            "CSS personnalisé (avancé)",
            placeholder=".message-assistant { font-family: 'Georgia', serif; }",
            height=100,
            help=(
                "Injecté en plus du style généré par les couleurs/police ci-dessus. "
                "Laisse vide si tu ne sais pas ce que c'est — les champs simples suffisent "
                "largement dans la majorité des cas."
            ),
        )

    st.divider()
    bouton_soumission = st.form_submit_button("🚀 Créer mon agent", use_container_width=True)


if bouton_soumission:
    erreurs = []
    if not nom_agent.strip():
        erreurs.append("Le nom de l'agent est obligatoire.")
    if not posture_generale.strip() and not limites_globales.strip():
        erreurs.append(
            "Remplis au moins la posture générale ou les limites globales, pour que "
            "l'agent ait un minimum de comportement défini."
        )
    if not icone_agent.strip():
        icone_agent = "🤖"

    if erreurs:
        for e in erreurs:
            st.error(e)
    else:
        agent_id = generer_id_depuis_nom(nom_agent)

        # Vérifie l'unicité de l'id avant d'insérer, pour donner un message
        # clair plutôt que de laisser Supabase renvoyer une erreur de clé
        # primaire dupliquée brute.
        try:
            existe_deja = (
                supabase.table("agents")
                .select("id")
                .eq("id", agent_id)
                .maybe_single()
                .execute()
            )
        except Exception as e:
            existe_deja = None
            logging.error(f"ERREUR SUPABASE (vérification unicité agent_id={agent_id}) : {e}")

        if existe_deja and existe_deja.data:
            st.error(
                f"Un agent existe déjà avec un nom trop proche (id généré: `{agent_id}`). "
                "Choisis un nom légèrement différent."
            )
        else:
            system_prompt = composer_system_prompt(
                ton, posture_generale, limites_globales, lignes_comportement,
                type_connaissance, description_connaissance,
                nom=nom_agent,
            )

            notion_page_id = extraire_id_notion(lien_notion)

            ui_config = {
                "titre_page": nom_agent.strip(),
                "icone_page": icone_agent.strip(),
                "titre_accueil": f"{icone_agent.strip()} {nom_agent.strip()}",
                "sous_titre_accueil": sous_titre.strip(),
                # Style du titre d'accueil (chantier thème). Un seul des deux
                # est vraiment utilisé par chat.py selon lequel est non-None :
                # titre_couleurs_lettres a priorité s'il est rempli (voir
                # chat.py, rendu du titre). "#000000" pour titre_couleur_unique
                # = pas de couleur personnalisée (comportement historique,
                # rendu Streamlit par défaut selon le thème clair/sombre).
                "titre_couleur_unique": titre_couleur_unique if style_titre == "Couleur unique" else "#000000",
                "titre_couleurs_lettres": titre_couleurs_lettres,
                "emoji_reponse": icone_agent.strip(),
                "placeholder_saisie": placeholder_saisie.strip() or "Pose ta question...",
                # Point 5 (Interface) du cadre de conception. couleur_fond,
                # couleur_accent, police, css_avance et rendu_visuel sont
                # lus par faces/app_etudiant.py. raisonnement_visible et
                # memoire_visible sont enregistrés mais pas encore
                # appliqués (aucune fonctionnalité correspondante n'existe
                # encore côté chat).
                "raisonnement_visible": raisonnement_visible,
                "rendu_visuel": rendu_visuel,
                "memoire_visible": memoire_visible,
                "couleur_fond": couleur_fond,
                "couleur_accent": couleur_accent,
                "couleur_bulle_assistant": couleur_bulle_assistant,
                "bulle_assistant_visible": bulle_assistant_visible,
                "couleur_bordure": couleur_bordure,
                "couleur_fond_page": couleur_fond_page,
                "couleur_texte_utilisateur": couleur_texte_utilisateur,
                "couleur_texte_assistant": couleur_texte_assistant,
                "couleur_texte_bouton": couleur_texte_bouton,
                "couleur_lien": couleur_lien,
                "couleur_bouton_fond": couleur_bouton_fond,
                "rayon_bulles": rayon_bulles,
                "taille_texte": taille_texte,
                "police": police,
                "css_avance": css_avance.strip(),
            }

            knowledge_source = {
                "type": type_connaissance,
                "description": description_connaissance.strip(),
                # Conservé tel quel (pas seulement indexé) pour pouvoir être
                # réaffiché et modifié depuis faces/mes_agents.py.
                "texte_libre": texte_libre.strip(),
            }

            nouvelle_ligne = {
                "id": agent_id,
                "nom": nom_agent.strip(),
                "system_prompt": system_prompt,
                "ui_config": ui_config,
                "knowledge_source": knowledge_source,
                "tools_enabled": outils_choisis,
                "owner_id": user_id,
            }
            if notion_page_id:
                nouvelle_ligne["notion_page_id"] = notion_page_id

            try:
                supabase.table("agents").insert(nouvelle_ligne).execute()
                st.success("Agent créé avec succès !")

                url_base = get_secret("URL_RETOUR_APP")
                if url_base:
                    lien = f"{url_base.rstrip('/')}/?agent={agent_id}"
                    st.markdown(f"Ton agent est déjà accessible ici :\n\n{lien}")
                    st.code(lien, language=None)
                else:
                    # Filet de sécurité si le secret n'est pas configuré sur ce
                    # déploiement (ex: test en local) : on ne peut pas deviner
                    # l'URL publique, donc on redonne au moins le paramètre
                    # à coller soi-même plutôt que d'afficher un lien cassé.
                    logging.error("URL_RETOUR_APP absent : impossible de construire le lien complet de l'agent.")
                    st.warning(
                        "URL_RETOUR_APP n'est pas configuré sur ce déploiement, impossible d'afficher le lien complet. "
                        f"Ajoute manuellement `?agent={agent_id}` à l'URL de ton déploiement."
                    )
                    st.code(agent_id, language=None)

                # --- Indexation des sources de connaissance -----------------
                # Chacune est indépendante : un échec sur l'une (ex: PDF illisible)
                # n'empêche pas les autres, et n'annule jamais la création de
                # l'agent, déjà actée juste au-dessus.
                if fichier_pdf is not None:
                    with st.spinner("Indexation du PDF..."):
                        nom_stockage = f"{agent_id}__{fichier_pdf.name}"
                        chemin_temp = f"temp_{nom_stockage}"
                        try:
                            with open(chemin_temp, "wb") as f:
                                f.write(fichier_pdf.getvalue())
                            upload_document(chemin_temp, nom_stockage)
                            indexer_document(chemin_temp, nom_stockage, agent_id)
                            st.success(f"PDF « {fichier_pdf.name} » indexé.")
                        except Exception as e:
                            logging.error(f"ERREUR indexation PDF (agent_id={agent_id}) : {e}")
                            st.warning(
                                f"L'agent est créé, mais le PDF n'a pas pu être indexé "
                                f"({e}). Tu pourras réessayer depuis \"Mes agents\"."
                            )
                        finally:
                            if os.path.exists(chemin_temp):
                                os.remove(chemin_temp)

                if notion_page_id:
                    st.info(
                        "Page Notion enregistrée : elle sera indexée automatiquement par "
                        "la synchronisation périodique (jusqu'à quelques heures de délai), "
                        "à condition qu'elle soit partagée avec l'intégration Notion du projet."
                    )

                if texte_libre.strip():
                    with st.spinner("Indexation du texte libre..."):
                        try:
                            indexer_texte(agent_id, "texte-libre", texte_libre.strip())
                            st.success("Texte libre indexé.")
                        except Exception as e:
                            logging.error(f"ERREUR indexation texte libre (agent_id={agent_id}) : {e}")
                            st.warning(
                                f"L'agent est créé, mais le texte libre n'a pas pu être "
                                f"indexé ({e}). Tu pourras réessayer depuis \"Mes agents\"."
                            )

                st.caption("Note : un lien/sous-domaine dédié arrive dans une prochaine étape de la plateforme.")

                st.write("")
                if st.button("📂 Aller à Mes agents", use_container_width=True, key=f"vers_mes_agents_{agent_id}"):
                    st.switch_page("vues/mes_agents.py")
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (insertion agent {agent_id}) : {e}")
                st.error("Impossible de créer l'agent (erreur technique). Réessaie dans un instant.")
