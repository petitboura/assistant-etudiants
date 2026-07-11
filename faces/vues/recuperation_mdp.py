"""
Récupération de mot de passe oublié — logique PARTAGÉE entre creer_agent.py,
mes_agents.py (côté créateur) et chat.py (côté étudiant), pour ne pas
tripler le même formulaire.

Pourquoi le token n'est PAS consommé à la simple ouverture de la page
(point important, voir core/auth.py pour le détail) : beaucoup de clients
email (Gmail en tête) pré-visitent automatiquement les liens contenus
dans un email pour vérifier qu'ils ne sont pas malveillants. Si notre
lien de récupération validait le token dès qu'une requête HTTP l'atteint
(ce que fait le lien tout fait de Supabase, {{ .ConfirmationURL }}), ce
pré-chargement automatique grille le token avant même que la personne ne
clique elle-même -> erreur "otp_expired" systématique, même en cliquant
authentiquement quelques secondes après réception.

La parade (recommandée par Supabase, voir leur doc "Email prefetching") :
le lien email pointe vers NOTRE page avec le token en simple paramètre
d'URL (?token_hash=xxx&type=recovery), sans rien valider automatiquement.
Le token n'est consommé QUE lorsque la personne clique sur le bouton
"Confirmer" ci-dessous -> un pré-chargement automatique n'a plus aucun
effet, puisqu'il n'y a plus de clic.

Ça suppose d'avoir modifié le template "Reset Password" dans Supabase
(Authentication > Emails > Templates) pour utiliser :
    <a href="{{ .RedirectTo }}&token_hash={{ .TokenHash }}&type=recovery">
au lieu du lien par défaut {{ .ConfirmationURL }}. Nécessite un SMTP
personnalisé activé sur le projet Supabase (l'éditeur de template est
verrouillé sans ça).
"""

import os
import sys

import streamlit as st

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))

from auth import etablir_session_depuis_token_hash, mettre_a_jour_mot_de_passe  # noqa: E402


def gerer_recuperation_mot_de_passe():
    """
    À appeler tout en haut de la page, avant l'UI de connexion normale.

    Retourne True si un flux de réinitialisation est en cours (l'appelant
    doit alors faire `st.stop()` juste après, le formulaire a déjà été
    affiché ici) ; False sinon (rien à faire, continuer le rendu normal).
    """
    query = st.query_params
    token_hash = query.get("token_hash")
    type_lien = query.get("type")

    if type_lien != "recovery" or not token_hash:
        return False

    st.markdown("### 🔑 Réinitialisation du mot de passe")

    if not st.session_state.get("session_recuperation_etablie"):
        st.write(
            "Clique pour confirmer cette demande de réinitialisation "
            "(cette étape protège contre les liens pré-ouverts "
            "automatiquement par certaines messageries)."
        )
        if st.button("Confirmer et continuer", key="btn_confirmer_recuperation"):
            succes, resultat = etablir_session_depuis_token_hash(token_hash)
            if succes:
                st.session_state.session_recuperation_etablie = True
                st.rerun()
            else:
                st.error(resultat)
        return True

    nouveau_mdp = st.text_input("Nouveau mot de passe", type="password", key="nouveau_mdp_recup")
    confirmation_mdp = st.text_input("Confirme le mot de passe", type="password", key="confirmation_mdp_recup")

    if st.button("Mettre à jour le mot de passe", key="btn_maj_mdp"):
        if not nouveau_mdp or nouveau_mdp != confirmation_mdp:
            st.error("Les deux mots de passe doivent être identiques et non vides.")
        else:
            succes, message = mettre_a_jour_mot_de_passe(nouveau_mdp)
            if succes:
                st.success(f"{message} Tu peux maintenant te connecter avec ton nouveau mot de passe ci-dessous.")
                # Vide les tokens de l'URL : sans ça, un simple rafraîchissement
                # de la page relancerait ce même flux de récupération.
                st.query_params.clear()
                st.session_state.pop("session_recuperation_etablie", None)
            else:
                st.error(message)

    return True
