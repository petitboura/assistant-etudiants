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
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'indexers'))

from auth import inscription, connexion, deconnexion  # noqa: E402
from index_documents import indexer_document, indexer_texte  # noqa: E402
from storage import upload_document  # noqa: E402

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
    page_title="Créer un agent — djiguigne",
    page_icon="🧩",
    layout="centered",
)

st.title("🧩 Créer ton agent IA")
st.caption("Configure ton assistant, il sera accessible immédiatement — aucun déploiement de ton côté.")


# --- Connexion obligatoire --------------------------------------------
# Réutilise core/auth.py tel quel (déjà en place, email/mdp + Google).
# Un agent créé sans utilisateur connecté n'aurait pas de owner_id, donc
# personne ne pourrait le retrouver dans "mes agents" plus tard (Étape 3).
if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

if not st.session_state.session_utilisateur:
    st.info("Connecte-toi pour créer un agent — c'est ce qui te permettra de le retrouver et le modifier plus tard.")

    onglet_connexion, onglet_inscription = st.tabs(["Se connecter", "Créer un compte"])

    with onglet_connexion:
        email = st.text_input("Email", key="email_connexion")
        mot_de_passe = st.text_input("Mot de passe", type="password", key="mdp_connexion")
        if st.button("Se connecter", key="btn_connexion"):
            succes, resultat = connexion(email, mot_de_passe)
            if succes:
                st.session_state.session_utilisateur = resultat
                st.rerun()
            else:
                st.error(resultat)

    with onglet_inscription:
        email_new = st.text_input("Email", key="email_inscription")
        mdp_new = st.text_input("Mot de passe", type="password", key="mdp_inscription")
        if st.button("Créer mon compte", key="btn_inscription"):
            succes, message = inscription(email_new, mdp_new)
            if succes:
                st.success(message)
            else:
                st.error(message)

    st.stop()


# --- Utilisateur connecté : formulaire de création ---------------------
user_id = st.session_state.session_utilisateur.user.id
email_utilisateur = st.session_state.session_utilisateur.user.email

col_a, col_b = st.columns([4, 1])
with col_a:
    st.caption(f"Connecté en tant que **{email_utilisateur}**")
with col_b:
    if st.button("Déconnexion"):
        deconnexion()
        st.session_state.session_utilisateur = None
        st.rerun()

st.divider()

# Nombre de lignes proposées pour le point 2 (comportement situationnel).
# Fixe et non dynamique car un st.form Streamlit ne peut pas ajouter de
# champs à la volée sans se soumettre — 4 couvre largement un premier
# jet (ex: "Exemple / Démonstration / Cours / Exercice" pour un tuteur).
# Les lignes laissées vides sont simplement ignorées à la composition.
NB_LIGNES_COMPORTEMENT = 4

with st.form("formulaire_creation_agent"):
    st.subheader("1. Identité de base")
    st.caption(
        "Ce qui reste stable quel que soit le type d'interaction : le ton, "
        "les limites, la posture générale de l'agent."
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
    col_fond, col_accent = st.columns(2)
    with col_fond:
        couleur_fond = st.color_picker(
            "Couleur des bulles de message",
            "#646464",
            help="Couleur de fond des bulles de message de l'utilisateur.",
        )
    with col_accent:
        couleur_accent = st.color_picker(
            "Couleur d'accent",
            "#8B5E3C",
            help="Utilisée pour les éléments mis en avant (ex: liens, boutons).",
        )

    police = st.selectbox(
        "Police du texte des réponses",
        ["Lora (serif, actuelle)", "Police système (sans-serif)"],
        help="Le style typographique des réponses de l'agent.",
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


def _generer_id_depuis_nom(nom):
    """
    Transforme "Coach fitness" en "coach-fitness". Doit rester unique dans
    la table `agents` (clé primaire texte) -> voir vérification plus bas.
    """
    slug = nom.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _extraire_id_notion(lien_ou_id):
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


def _composer_system_prompt(
    ton, posture_generale, limites_globales, lignes_comportement,
    type_connaissance, description_connaissance,
):
    """
    Assemble les champs structurés des points 1, 2 et 4 du cadre de
    conception en UN SEUL texte brut (jamais de JSON) : c'est ce texte,
    et rien d'autre, qui est envoyé au LLM comme system prompt (voir
    core/configuration.py, colonne agents.system_prompt).

    Reste modifiable tel quel ensuite par le créateur depuis "Mes
    agents" (faces/mes_agents.py) — composition automatique à la
    création, texte libre éditable après coup.
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
        agent_id = _generer_id_depuis_nom(nom_agent)

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
            system_prompt = _composer_system_prompt(
                ton, posture_generale, limites_globales, lignes_comportement,
                type_connaissance, description_connaissance,
            )

            notion_page_id = _extraire_id_notion(lien_notion)

            ui_config = {
                "titre_page": nom_agent.strip(),
                "icone_page": icone_agent.strip(),
                "titre_accueil": f"{icone_agent.strip()} {nom_agent.strip()}",
                "sous_titre_accueil": sous_titre.strip(),
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
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (insertion agent {agent_id}) : {e}")
                st.error("Impossible de créer l'agent (erreur technique). Réessaie dans un instant.")
