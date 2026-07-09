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
import logging

import streamlit as st
from supabase import create_client

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

try:
    from registre_outils import SERVEURS_MCP
    OUTILS_DISPONIBLES = [s["nom"] for s in SERVEURS_MCP]
except Exception as e:
    logging.error(f"ERREUR import registre_outils : {e}")
    OUTILS_DISPONIBLES = []

st.set_page_config(page_title="Mes agents — djiguigne", page_icon="📂", layout="centered")
st.title("📂 Mes agents")


# --- Connexion obligatoire (identique à creer_agent.py) ----------------
if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

if not st.session_state.session_utilisateur:
    st.info("Connecte-toi pour voir et gérer tes agents.")

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
    st.info(
        "Tu n'as pas encore créé d'agent. Rends-toi sur la page de création "
        "pour en configurer un premier."
    )
    st.stop()

for agent in mes_agents:
    ui_config = agent.get("ui_config") or {}
    icone = ui_config.get("icone_page", "🤖")
    statut = "🟢 actif" if agent.get("actif", True) else "⚪ désactivé"

    with st.expander(f"{icone} {agent['nom']} — {statut}"):
        with st.form(f"formulaire_edition_{agent['id']}"):
            nouveau_nom = st.text_input("Nom", value=agent["nom"], key=f"nom_{agent['id']}")
            nouvelle_icone = st.text_input(
                "Icône", value=icone, max_chars=2, key=f"icone_{agent['id']}"
            )
            nouveau_sous_titre = st.text_area(
                "Phrase d'accueil",
                value=ui_config.get("sous_titre_accueil", ""),
                height=70,
                key=f"soustitre_{agent['id']}",
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
