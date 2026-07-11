"""
Face créateur — "Mes agents" (plateforme djiguigne).

ÉTAPE 3 du plan multi-agent : un créateur connecté voit la liste de ses
agents (owner_id = lui), peut modifier leur configuration, et les
désactiver (suppression douce -> colonne agents.actif, voir migration
add_actif_column_to_agents).

Pourquoi suppression douce et pas suppression réelle : `conversations` et
`conversation_summaries` référencent `agents.id` par clé étrangère.
Supprimer réellement une ligne `agents` casserait ces historiques (ou
échouerait selon la contrainte). Désactiver (actif=false) coupe l'accès à
l'agent (voir faces/app_etudiant.py, vérification ajoutée à l'étape 3)
sans perdre de données, et reste réversible en cas d'erreur.
"""

import os
import sys
import re
import logging

import streamlit as st
from supabase import create_client

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'indexers'))
# Le dossier vues/ lui-même : st.navigation() exécute cette page via exec(),
# ce qui n'ajoute PAS automatiquement son propre dossier à sys.path
# (contrairement à un lancement direct `python vues/mes_agents.py`) -> sans
# cette ligne, l'import de theme_djiguigne.py juste à côté échoue avec
# ModuleNotFoundError.
sys.path.append(os.path.dirname(__file__))

from auth import inscription, connexion, deconnexion, demarrer_reinitialisation_mot_de_passe  # noqa: E402
from index_documents import indexer_document, indexer_texte, supprimer_chunks_existants  # noqa: E402
from storage import upload_document, list_documents, delete_document, get_document_url  # noqa: E402
from themes import POLICES_AFFICHEES, POLICE_PAR_DEFAUT, RAYONS, RAYON_PAR_DEFAUT, TAILLES, TAILLE_PAR_DEFAUT  # noqa: E402
from recuperation_mdp import gerer_recuperation_mot_de_passe  # noqa: E402

# Identité visuelle Djiguignè AI (voir vues/theme_djiguigne.py) : restyle
# uniquement l'habillage, aucune logique métier ci-dessous n'est modifiée.
from theme_djiguigne import injecter_theme, afficher_entete  # noqa: E402

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


def _hex_sur(valeur, defaut):
    """
    st.color_picker exige un hex valide ("#RRGGBB") pour son paramètre
    value. couleur_lien/couleur_bouton_fond peuvent valoir "" en base
    (réglage optionnel non activé, voir creer_agent.py) -> ce garde-fou
    retombe sur un défaut sûr plutôt que de crasher le formulaire.
    """
    if isinstance(valeur, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", valeur):
        return valeur
    return defaut


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

try:
    from registre_outils import SERVEURS_MCP
    OUTILS_DISPONIBLES = [s["nom"] for s in SERVEURS_MCP]
except Exception as e:
    logging.error(f"ERREUR import registre_outils : {e}")
    OUTILS_DISPONIBLES = []

st.set_page_config(page_title="Mes agents — Djiguignè AI", page_icon="📂", layout="centered")
injecter_theme()


# --- Connexion obligatoire (identique à creer_agent.py) ----------------
if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

if not st.session_state.session_utilisateur:
    if gerer_recuperation_mot_de_passe():
        st.stop()

    afficher_entete()
    st.markdown(
        """
        <div style="text-align:center; margin: 0.5rem 0 1.8rem 0; animation: dj-fade-up 0.5s ease both;">
            <h1 class="dj-display" style="font-size:2.1rem; margin-bottom:0.3rem;">Tes agents t'attendent</h1>
            <p style="color:var(--dj-texte-muet); font-size:1.02rem; max-width:480px; margin:0 auto;">
                Connecte-toi pour retrouver, modifier et suivre tous les
                assistants que tu as créés.
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
                st.rerun()
            else:
                st.error(resultat)

        with st.expander("Mot de passe oublié ?"):
            email_oublie = st.text_input("Ton email", key="email_mdp_oublie")
            if st.button("Envoyer le lien de réinitialisation", key="btn_mdp_oublie"):
                _, message = demarrer_reinitialisation_mot_de_passe(
                    email_oublie, redirection=get_secret("URL_RETOUR_APP")
                )
                st.info(message)

    with onglet_inscription:
        email_new = st.text_input("Email", key="email_inscription")
        mdp_new = st.text_input("Mot de passe", type="password", key="mdp_inscription")
        if st.button("Créer mon compte", key="btn_inscription", use_container_width=True):
            # Même logique que creer_agent.py : le créateur doit revenir sur
            # la plateforme après confirmation, pas sur un agent précis.
            succes, resultat = inscription(email_new, mdp_new, redirection=get_secret("URL_RETOUR_APP"))
            if succes:
                if hasattr(resultat, "user"):
                    # Session directement valide (confirmation email
                    # désactivée sur ce projet) : nouveau compte sans agent
                    # -> on redirige vers la création du premier, plutôt
                    # que de rester ici sur un tableau de bord vide.
                    st.session_state.session_utilisateur = resultat
                    st.switch_page("vues/creer_agent.py")
                else:
                    st.success(resultat)
            else:
                st.error(resultat)

    st.stop()


user_id = st.session_state.session_utilisateur.user.id
email_utilisateur = st.session_state.session_utilisateur.user.email


def _deconnexion_mes_agents():
    deconnexion()
    st.session_state.session_utilisateur = None
    st.rerun()


afficher_entete(email_utilisateur=email_utilisateur, sur_clic_deconnexion=_deconnexion_mes_agents)

col_titre, col_action = st.columns([3, 1.4])
with col_titre:
    st.markdown('<h1 class="dj-display" style="font-size:1.7rem;">📂 Tableau de bord</h1>', unsafe_allow_html=True)
with col_action:
    st.markdown("<div style='height:0.4rem'></div>", unsafe_allow_html=True)
    if st.button("➕ Nouvel agent", use_container_width=True, key="btn_nouvel_agent_dashboard"):
        st.switch_page("vues/creer_agent.py")


def _charger_mes_agents(user_id):
    try:
        res = (
            supabase.table("agents")
            .select(
                "id, nom, ui_config, tools_enabled, system_prompt, actif, "
                "created_at, knowledge_source, notion_page_id"
            )
            .eq("owner_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (chargement agents pour owner_id={user_id}) : {e}")
        st.error("Impossible de charger tes agents pour le moment.")
        return []


def _extraire_id_notion(lien_ou_id):
    """Voir faces/creer_agent.py — même logique, dupliquée volontairement
    (fichiers Streamlit indépendants, pas de module partagé entre les deux)."""
    import re
    if not lien_ou_id or not lien_ou_id.strip():
        return None
    hex_seul = re.sub(r"[^a-f0-9]", "", lien_ou_id.strip().lower())
    if len(hex_seul) < 32:
        return None
    brut = hex_seul[-32:]
    return f"{brut[0:8]}-{brut[8:12]}-{brut[12:16]}-{brut[16:20]}-{brut[20:32]}"


mes_agents = _charger_mes_agents(user_id)

if not mes_agents:
    st.markdown(
        """
        <div style="text-align:center; padding: 3.5rem 1rem; border:1px dashed var(--dj-bordure);
                    border-radius:16px; margin-top:1rem; animation: dj-fade-up 0.5s ease both;">
            <div style="font-size:2.2rem; margin-bottom:0.6rem;">🧩</div>
            <h3 class="dj-display" style="margin-bottom:0.3rem;">Aucun agent pour l'instant</h3>
            <p style="color:var(--dj-texte-muet);">Ton premier assistant sera en ligne en quelques minutes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    if st.button("🧩 Créer mon premier agent", use_container_width=True):
        st.switch_page("vues/creer_agent.py")
    st.stop()

# --- Bandeau de statistiques -------------------------------------------
# Purement informatif (aucune écriture), calculé à partir de ce qui est
# déjà chargé ci-dessus -> pas de requête Supabase supplémentaire.
_nb_total = len(mes_agents)
_nb_actifs = sum(1 for a in mes_agents if a.get("actif", True))
_nb_inactifs = _nb_total - _nb_actifs

st.markdown(
    f"""
    <div style="display:grid; grid-template-columns: repeat(3, 1fr); gap:12px; margin: 1.2rem 0 1.8rem 0;">
        <div class="dj-stat-card" style="background:var(--dj-surface); border:1px solid var(--dj-bordure);
                    border-radius:14px; padding:1rem 1.2rem;">
            <div style="font-family:'JetBrains Mono',monospace; font-size:1.6rem; font-weight:600; color:var(--dj-texte);">{_nb_total}</div>
            <div style="color:var(--dj-texte-muet); font-size:0.82rem;">Agents au total</div>
        </div>
        <div class="dj-stat-card" style="background:var(--dj-surface); border:1px solid var(--dj-bordure);
                    border-radius:14px; padding:1rem 1.2rem; animation-delay:0.06s;">
            <div style="font-family:'JetBrains Mono',monospace; font-size:1.6rem; font-weight:600; color:var(--dj-succes);">{_nb_actifs}</div>
            <div style="color:var(--dj-texte-muet); font-size:0.82rem;">Actifs</div>
        </div>
        <div class="dj-stat-card" style="background:var(--dj-surface); border:1px solid var(--dj-bordure);
                    border-radius:14px; padding:1rem 1.2rem; animation-delay:0.12s;">
            <div style="font-family:'JetBrains Mono',monospace; font-size:1.6rem; font-weight:600; color:var(--dj-inactif);">{_nb_inactifs}</div>
            <div style="color:var(--dj-texte-muet); font-size:0.82rem;">Désactivés</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

for agent in mes_agents:
    ui_config = agent.get("ui_config") or {}
    icone = ui_config.get("icone_page", "🤖")
    actif = agent.get("actif", True)
    statut_html = (
        '<span class="dj-badge dj-badge-actif"><span class="dj-badge-dot"></span>actif</span>'
        if actif
        else '<span class="dj-badge dj-badge-inactif"><span class="dj-badge-dot"></span>désactivé</span>'
    )

    with st.expander(f"{icone}  {agent['nom']}"):
        st.markdown(statut_html, unsafe_allow_html=True)
        st.write("")
        url_base = get_secret("URL_RETOUR_APP")
        if url_base:
            lien_agent = f"{url_base.rstrip('/')}/?agent={agent['id']}"
            st.caption("Lien de ton agent (à partager)")
            col_lien, col_voir = st.columns([3, 1.3])
            with col_lien:
                st.code(lien_agent, language=None)
            with col_voir:
                st.link_button("👁️ Voir mon agent", lien_agent, use_container_width=True)
        else:
            # Filet de sécurité si le secret n'est pas configuré sur ce
            # déploiement (ex: test en local) : on ne peut pas deviner
            # l'URL publique, donc on redonne au moins l'id à coller
            # soi-même plutôt que de ne rien afficher du tout.
            logging.error("URL_RETOUR_APP absent : impossible d'afficher le lien complet dans Mes agents.")
            st.caption("Identifiant de ton agent (URL_RETOUR_APP non configuré, lien complet indisponible)")
            st.code(agent["id"], language=None)

        # Hors du st.form (comme dans creer_agent.py) : nouveau_nom et
        # nouvelle_icone doivent être connus à jour pour générer le bon
        # nombre de color pickers du titre multicolore juste en dessous,
        # et bulle_assistant_visible doit pouvoir cacher/afficher
        # instantanément le color picker de bulle assistant plus bas dans
        # le form -> tout ce qui conditionne l'affichage d'autre chose
        # doit rester hors du form, un widget dedans ne redéclenche rien
        # tant que le formulaire n'est pas soumis.
        nouveau_nom = st.text_input("Nom", value=agent["nom"], key=f"nom_{agent['id']}")
        nouvelle_icone = st.text_input(
            "Icône", value=icone, max_chars=2, key=f"icone_{agent['id']}"
        )

        st.markdown("**🎨 Style visuel — aperçu en direct**")
        bulle_assistant_visible = st.checkbox(
            "Afficher les réponses dans une bulle visible",
            value=ui_config.get("bulle_assistant_visible", True),
            key=f"bulle_visible_{agent['id']}",
            help="Décoche pour un style texte libre, sans encadré autour des réponses (comme ChatGPT/Claude).",
        )

        _titre_complet_apercu = f"{nouvelle_icone.strip()} {nouveau_nom.strip()}".strip()
        _couleurs_lettres_actuelles = ui_config.get("titre_couleurs_lettres")
        _etait_multicolore = bool(
            _couleurs_lettres_actuelles
            and len(_couleurs_lettres_actuelles) == len(ui_config.get("titre_accueil", ""))
        )

        st.markdown("**Style du titre d'accueil**")
        style_titre = st.radio(
            "Style du titre d'accueil",
            ["Couleur unique", "Multicolore (chaque lettre)"],
            index=1 if _etait_multicolore else 0,
            horizontal=True,
            label_visibility="collapsed",
            key=f"style_titre_{agent['id']}",
        )

        if style_titre == "Multicolore (chaque lettre)":
            if not _titre_complet_apercu:
                st.caption("Remplis le nom ci-dessus pour choisir une couleur par lettre.")
                nouveau_titre_couleurs_lettres = None
            else:
                st.caption(f"Une couleur par caractère de \"{_titre_complet_apercu}\" :")
                palette_defaut = [
                    "#E63946", "#F1A208", "#2A9D8F", "#264653", "#8338EC",
                    "#FF006E", "#3A86FF", "#06D6A0", "#EF476F", "#FFD166",
                ]
                colonnes_lettres = st.columns(min(len(_titre_complet_apercu), 10))
                nouveau_titre_couleurs_lettres = []
                for i, caractere in enumerate(_titre_complet_apercu):
                    # Réutilise la couleur déjà enregistrée pour cette
                    # position si elle existe (agent déjà en mode
                    # multicolore, juste réouvert), sinon retombe sur la
                    # palette par défaut -> évite de tout réinitialiser au
                    # hasard à chaque ouverture du formulaire d'édition.
                    defaut_position = (
                        _couleurs_lettres_actuelles[i]
                        if _etait_multicolore and i < len(_couleurs_lettres_actuelles)
                        else palette_defaut[i % len(palette_defaut)]
                    )
                    with colonnes_lettres[i % len(colonnes_lettres)]:
                        couleur_lettre = st.color_picker(
                            caractere if caractere.strip() else "␣",
                            defaut_position,
                            key=f"titre_lettre_{agent['id']}_{i}",
                        )
                    nouveau_titre_couleurs_lettres.append(couleur_lettre)
        else:
            nouveau_titre_couleur_unique = st.color_picker(
                "Couleur du titre",
                _hex_sur(ui_config.get("titre_couleur_unique"), "#000000"),
                key=f"titre_couleur_unique_{agent['id']}",
                help="\"#000000\" = pas de couleur personnalisée (rendu par défaut).",
            )
            nouveau_titre_couleurs_lettres = None

        with st.form(f"formulaire_edition_{agent['id']}"):
            nouveau_sous_titre = st.text_area(
                "Phrase d'accueil",
                value=ui_config.get("sous_titre_accueil", ""),
                height=70,
                key=f"soustitre_{agent['id']}",
            )

            st.caption("Thème visuel")
            st.markdown("*Arrière-plans*")
            st.caption("↑ La visibilité de la bulle assistant se règle juste au-dessus (\"Style visuel — aperçu en direct\").")
            col_fond_page, col_fond_bulle, col_fond_assistant = st.columns(3)
            with col_fond_page:
                nouvelle_couleur_fond_page = st.color_picker(
                    "Fond de la page",
                    value=_hex_sur(ui_config.get("couleur_fond_page"), "#FFFFFF"),
                    key=f"couleur_fond_page_{agent['id']}",
                )
            with col_fond_bulle:
                nouvelle_couleur_fond = st.color_picker(
                    "Fond — bulle utilisateur",
                    value=_hex_sur(ui_config.get("couleur_fond"), "#646464"),
                    key=f"couleur_fond_{agent['id']}",
                )
            with col_fond_assistant:
                if bulle_assistant_visible:
                    nouvelle_couleur_bulle_assistant = st.color_picker(
                        "Fond — bulle assistant",
                        value=_hex_sur(ui_config.get("couleur_bulle_assistant"), "#FFFFFF"),
                        key=f"couleur_bulle_assistant_{agent['id']}",
                    )
                else:
                    nouvelle_couleur_bulle_assistant = "transparent"
                    st.caption("🫥 Bulle masquée (style texte libre)")

            st.markdown("*Texte*")
            col_texte_user, col_texte_assistant, col_texte_bouton = st.columns(3)
            with col_texte_user:
                nouvelle_couleur_texte_utilisateur = st.color_picker(
                    "Texte — bulle utilisateur",
                    value=_hex_sur(ui_config.get("couleur_texte_utilisateur"), "#FFFFFF"),
                    key=f"couleur_texte_user_{agent['id']}",
                )
            with col_texte_assistant:
                nouvelle_couleur_texte_assistant = st.color_picker(
                    "Texte — bulle assistant",
                    value=_hex_sur(ui_config.get("couleur_texte_assistant"), "#000000"),
                    key=f"couleur_texte_assistant_{agent['id']}",
                )
            with col_texte_bouton:
                nouvelle_couleur_texte_bouton = st.color_picker(
                    "Texte des boutons",
                    value=_hex_sur(ui_config.get("couleur_texte_bouton"), "#FFFFFF"),
                    key=f"couleur_texte_bouton_{agent['id']}",
                    help="Doit contraster avec la couleur d'accent ci-dessous.",
                )

            st.markdown("*Accent, bordures, forme*")
            nouvelle_couleur_accent = st.color_picker(
                "Couleur d'accent (liens + boutons, sauf réglage séparé ci-dessous)",
                value=_hex_sur(ui_config.get("couleur_accent"), "#8B5E3C"),
                key=f"couleur_accent_{agent['id']}",
            )

            _lien_actuel = _hex_sur(ui_config.get("couleur_lien"), None)
            _bouton_actuel = _hex_sur(ui_config.get("couleur_bouton_fond"), None)
            distinguer_lien_bouton = st.checkbox(
                "Utiliser une couleur différente pour les liens et pour les boutons",
                value=bool(_lien_actuel or _bouton_actuel),
                key=f"distinguer_{agent['id']}",
            )
            nouvelle_couleur_lien = ""
            nouvelle_couleur_bouton_fond = ""
            if distinguer_lien_bouton:
                col_lien, col_bouton = st.columns(2)
                with col_lien:
                    nouvelle_couleur_lien = st.color_picker(
                        "Couleur des liens",
                        value=_lien_actuel or "#8B5E3C",
                        key=f"couleur_lien_{agent['id']}",
                    )
                with col_bouton:
                    nouvelle_couleur_bouton_fond = st.color_picker(
                        "Couleur de fond des boutons",
                        value=_bouton_actuel or "#8B5E3C",
                        key=f"couleur_bouton_fond_{agent['id']}",
                    )

            col_bordure, col_rayon, col_taille = st.columns(3)
            with col_bordure:
                nouvelle_couleur_bordure = st.color_picker(
                    "Couleur des bordures",
                    value=_hex_sur(ui_config.get("couleur_bordure"), "#808080"),
                    key=f"couleur_bordure_{agent['id']}",
                )
            with col_rayon:
                _rayon_actuel = ui_config.get("rayon_bulles", RAYON_PAR_DEFAUT)
                if _rayon_actuel not in RAYONS:
                    _rayon_actuel = RAYON_PAR_DEFAUT
                nouveau_rayon_bulles = st.selectbox(
                    "Arrondi des bulles",
                    list(RAYONS.keys()),
                    index=list(RAYONS.keys()).index(_rayon_actuel),
                    key=f"rayon_{agent['id']}",
                )
            with col_taille:
                _taille_actuelle = ui_config.get("taille_texte") or TAILLE_PAR_DEFAUT
                if _taille_actuelle not in TAILLES:
                    _taille_actuelle = TAILLE_PAR_DEFAUT
                nouvelle_taille_texte = st.selectbox(
                    "Taille du texte",
                    list(TAILLES.keys()),
                    index=list(TAILLES.keys()).index(_taille_actuelle),
                    key=f"taille_{agent['id']}",
                )

            _police_actuelle = ui_config.get("police", POLICE_PAR_DEFAUT)
            if _police_actuelle not in POLICES_AFFICHEES:
                # Gère les alias historiques ("Lora (serif, actuelle)", etc.)
                # qui ne sont pas dans la liste affichée : on retombe sur le
                # nouveau libellé équivalent plutôt que de planter sur un
                # index introuvable dans POLICES_AFFICHEES.
                _police_actuelle = POLICE_PAR_DEFAUT
            nouvelle_police = st.selectbox(
                "Police du texte des réponses",
                POLICES_AFFICHEES,
                index=POLICES_AFFICHEES.index(_police_actuelle),
                key=f"police_{agent['id']}",
            )

            nouveau_prompt = st.text_area(
                "System prompt (comportement)",
                value=agent.get("system_prompt") or "",
                height=180,
                key=f"prompt_{agent['id']}",
                help=(
                    "Vide si cet agent utilise encore Notion comme source "
                    "(agents historiques) — le modifier ici bascule l'agent "
                    "sur ce texte directement."
                ),
            )

            outils_actuels = set(agent.get("tools_enabled") or [])
            nouveaux_outils = []
            if OUTILS_DISPONIBLES:
                st.caption("Outils activés")
                for nom_outil in OUTILS_DISPONIBLES:
                    coche = st.checkbox(
                        nom_outil,
                        value=(nom_outil in outils_actuels),
                        key=f"outil_{agent['id']}_{nom_outil}",
                    )
                    if coche:
                        nouveaux_outils.append(nom_outil)

            knowledge_source = agent.get("knowledge_source") or {}
            st.caption("Base de connaissance")
            nouveau_lien_notion = st.text_input(
                "Lien ou ID d'une page Notion",
                value=agent.get("notion_page_id") or "",
                key=f"notion_{agent['id']}",
                help="Indexée automatiquement par la synchronisation périodique existante.",
            )
            nouveau_texte_libre = st.text_area(
                "Texte de connaissance libre",
                value=knowledge_source.get("texte_libre", ""),
                height=150,
                key=f"texte_libre_{agent['id']}",
                help="Réindexé automatiquement à chaque enregistrement de ce formulaire.",
            )
            nouveau_pdf = st.file_uploader(
                "Remplacer/ajouter un document PDF",
                type=["pdf"],
                key=f"pdf_{agent['id']}",
                help="Laisse vide pour ne rien changer au PDF déjà indexé.",
            )

            col_save, col_toggle = st.columns(2)
            with col_save:
                enregistrer = st.form_submit_button("💾 Enregistrer", use_container_width=True)
            with col_toggle:
                label_toggle = "🗑️ Désactiver" if agent.get("actif", True) else "♻️ Réactiver"
                basculer_statut = st.form_submit_button(label_toggle, use_container_width=True)

        # --- Documents PDF indexés ------------------------------------
        # En dehors du st.form ci-dessus : un st.form ne peut contenir que
        # des st.form_submit_button, pas de boutons "Ouvrir"/"Supprimer"
        # indépendants par ligne.
        st.caption("📄 Documents indexés")
        prefixe_stockage = f"{agent['id']}__"
        try:
            tous_les_fichiers = list_documents()
        except Exception as e:
            logging.error(f"ERREUR SUPABASE STORAGE (liste documents, agent_id={agent['id']}) : {e}")
            tous_les_fichiers = []

        fichiers_agent = [f for f in tous_les_fichiers if f.startswith(prefixe_stockage)]

        if not fichiers_agent:
            st.caption("Aucun PDF indexé pour cet agent.")
        else:
            for nom_stockage in fichiers_agent:
                nom_affiche = nom_stockage[len(prefixe_stockage):]
                col_nom, col_ouvrir, col_supprimer = st.columns([3, 1.2, 1.2])
                with col_nom:
                    st.write(nom_affiche)
                with col_ouvrir:
                    try:
                        st.link_button("Ouvrir", get_document_url(nom_stockage), use_container_width=True)
                    except Exception as e:
                        logging.error(f"ERREUR SUPABASE STORAGE (url document {nom_stockage}) : {e}")
                with col_supprimer:
                    if st.button("🗑️ Supprimer", key=f"suppr_doc_{agent['id']}_{nom_stockage}", use_container_width=True):
                        try:
                            delete_document(nom_stockage)
                            # Supprime aussi les chunks vectorisés associés,
                            # sinon le RAG continuerait à retrouver le contenu
                            # d'un PDF qui n'existe plus dans le stockage.
                            supprimer_chunks_existants(agent["id"], nom_stockage)
                            st.success(f"« {nom_affiche} » supprimé.")
                            st.rerun()
                        except Exception as e:
                            logging.error(
                                f"ERREUR suppression document {nom_stockage} (agent_id={agent['id']}) : {e}"
                            )
                            st.error("Impossible de supprimer ce document.")

        if enregistrer:
            notion_page_id = _extraire_id_notion(nouveau_lien_notion)
            mise_a_jour = {
                "nom": nouveau_nom.strip(),
                "system_prompt": nouveau_prompt.strip(),
                "tools_enabled": nouveaux_outils,
                "notion_page_id": notion_page_id,
                "knowledge_source": {
                    **knowledge_source,
                    "texte_libre": nouveau_texte_libre.strip(),
                },
                "ui_config": {
                    **ui_config,
                    "titre_page": nouveau_nom.strip(),
                    "icone_page": nouvelle_icone.strip() or "🤖",
                    "titre_accueil": f"{nouvelle_icone.strip()} {nouveau_nom.strip()}",
                    "sous_titre_accueil": nouveau_sous_titre.strip(),
                    "emoji_reponse": nouvelle_icone.strip() or "🤖",
                    "couleur_fond": nouvelle_couleur_fond,
                    "couleur_accent": nouvelle_couleur_accent,
                    "couleur_bulle_assistant": nouvelle_couleur_bulle_assistant,
                    "bulle_assistant_visible": bulle_assistant_visible,
                    "titre_couleur_unique": (
                        nouveau_titre_couleur_unique if style_titre == "Couleur unique" else "#000000"
                    ),
                    "titre_couleurs_lettres": nouveau_titre_couleurs_lettres,
                    "couleur_bordure": nouvelle_couleur_bordure,
                    "couleur_fond_page": nouvelle_couleur_fond_page,
                    "couleur_texte_utilisateur": nouvelle_couleur_texte_utilisateur,
                    "couleur_texte_assistant": nouvelle_couleur_texte_assistant,
                    "couleur_texte_bouton": nouvelle_couleur_texte_bouton,
                    "couleur_lien": nouvelle_couleur_lien,
                    "couleur_bouton_fond": nouvelle_couleur_bouton_fond,
                    "rayon_bulles": nouveau_rayon_bulles,
                    "taille_texte": nouvelle_taille_texte,
                    "police": nouvelle_police,
                },
            }
            try:
                supabase.table("agents").update(mise_a_jour).eq("id", agent["id"]).eq(
                    "owner_id", user_id
                ).execute()
                st.success("Modifications enregistrées.")

                # Réindexation : même logique qu'à la création
                # (faces/creer_agent.py), un échec n'annule jamais la
                # sauvegarde des champs déjà actée juste au-dessus.
                if nouveau_texte_libre.strip():
                    try:
                        indexer_texte(agent["id"], "texte-libre", nouveau_texte_libre.strip())
                        st.success("Texte libre réindexé.")
                    except Exception as e:
                        logging.error(f"ERREUR réindexation texte libre (agent_id={agent['id']}) : {e}")
                        st.warning(f"Le texte a été enregistré, mais pas réindexé pour l'instant ({e}).")

                if nouveau_pdf is not None:
                    with st.spinner("Indexation du PDF..."):
                        nom_stockage = f"{agent['id']}__{nouveau_pdf.name}"
                        chemin_temp = f"temp_{nom_stockage}"
                        try:
                            with open(chemin_temp, "wb") as f:
                                f.write(nouveau_pdf.getvalue())
                            upload_document(chemin_temp, nom_stockage)
                            indexer_document(chemin_temp, nom_stockage, agent["id"])
                            st.success(f"PDF « {nouveau_pdf.name} » indexé.")
                        except Exception as e:
                            logging.error(f"ERREUR indexation PDF (agent_id={agent['id']}) : {e}")
                            st.warning(f"Le PDF n'a pas pu être indexé ({e}).")
                        finally:
                            if os.path.exists(chemin_temp):
                                os.remove(chemin_temp)

                st.rerun()
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (mise à jour agent {agent['id']}) : {e}")
                st.error("Impossible d'enregistrer les modifications.")

        if basculer_statut:
            nouvel_etat = not agent.get("actif", True)
            try:
                # .eq("owner_id", user_id) en plus de .eq("id", ...) : double
                # sécurité pour qu'un créateur ne puisse jamais modifier un
                # agent qui ne lui appartient pas, même en cas de bug côté UI.
                supabase.table("agents").update({"actif": nouvel_etat}).eq(
                    "id", agent["id"]
                ).eq("owner_id", user_id).execute()
                st.rerun()
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (changement statut agent {agent['id']}) : {e}")
                st.error("Impossible de changer le statut de l'agent.")
