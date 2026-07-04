"""
Face étudiant — interface Streamlit du coach mathématique.
"""

import sys
import os
import re
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import streamlit as st
import streamlit.components.v1 as components
from main import chat
from auth import inscription, connexion, deconnexion
from connexions.notion import demarrer_connexion_notion, finaliser_connexion_notion, etat_notion_en_attente, est_connecte

st.set_page_config(page_title="Votre coatch mathématique", page_icon="🎓", layout="centered")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600&display=swap');

    .message-user {
        background-color: rgba(100, 100, 100, 0.2);
        color: inherit;
        padding: 12px 18px;
        border-radius: 18px;
        margin: 8px 0;
        display: inline-block;
        max-width: 75%;
        float: right;
        text-align: right;
        border: 1px solid rgba(128,128,128,0.3);
    }

    .message-assistant {
        font-family: 'Lora', serif;
        color: inherit;
        padding: 10px 4px;
        margin: 8px 0;
        max-width: 85%;
        line-height: 1.7;
    }

    .clearfix { clear: both; }

    .statut-outil {
        font-family: 'Lora', serif;
        font-style: italic;
        font-size: 0.85em;
        color: rgba(128, 128, 128, 0.9);
        padding: 4px 4px;
        margin: 4px 0 0 0;
    }
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
    components.html(
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
        height=0,
        width=0,
    )


# --- Compte étudiant (optionnel) ---------------------------------------
# Le chat reste utilisable sans connexion. Ce bloc, dans la barre latérale,
# ne bloque jamais l'accès au coach : il propose juste de se connecter pour
# celles et ceux qui en ont besoin (ex: plus tard, connecter son Notion).

if "session_utilisateur" not in st.session_state:
    st.session_state.session_utilisateur = None

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

        with onglet_inscription:
            email_inscription = st.text_input("Email", key="email_inscription")
            mdp_inscription = st.text_input("Mot de passe", type="password", key="mdp_inscription")
            if st.button("Créer mon compte", key="bouton_inscription"):
                succes, message = inscription(email_inscription, mdp_inscription)
                if succes:
                    st.success(message)
                else:
                    st.error(message)
    else:
        email_connecte = st.session_state.session_utilisateur.user.email
        user_id_connecte = st.session_state.session_utilisateur.user.id
        st.markdown(f"Connecté : **{email_connecte}**")

        if "notion_message" in st.session_state:
            st.info(st.session_state.pop("notion_message"))

        st.markdown("---")
        if est_connecte(user_id_connecte):
            st.caption("📓 Notion connecté")
        else:
            st.caption("📓 Notion non connecté")
            if st.button("Connecter mon Notion"):
                url = demarrer_connexion_notion(user_id_connecte)
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
                f'<div class="message-assistant">{contenu_affiche}🎓</div><div class="clearfix"></div>',
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


if len(st.session_state.messages) == 0:
    st.title("🎓 Votre coatch mathématique")
    st.caption("Tout comprendre sur les maths. Je te donne rien, je t'enseigne tout.")

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

        generateur = chat(reprise={
            "etat_reprise": evenement_attente["etat_reprise"],
            "approuve": decision,
        })
        reponse_complete, nouvelle_attente = _consommer_flux(generateur, placeholder_statut, placeholder)

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


if prompt := st.chat_input("Pose ta question..."):
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

    generateur = chat(prompt, historique, user_id_courant)
    reponse_complete, evenement_confirmation = _consommer_flux(generateur, placeholder_statut, placeholder)

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
_typeset_mathjax()

if st.session_state.compteur >= 3:
    st.markdown("---")
    st.markdown("Ton avis compte, dis-nous ce que tu penses !")
    st.link_button("Remplir le formulaire", "https://forms.gle/zQPQsb9cX46188oh9")
