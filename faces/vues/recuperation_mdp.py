"""
Récupération de mot de passe oublié — logique PARTAGÉE entre creer_agent.py,
mes_agents.py (côté créateur) et chat.py (côté étudiant), pour ne pas
tripler le même bridge JS et le même formulaire.

Pourquoi un bridge JS est nécessaire (et ce n'est pas une bizarrerie
Streamlit, c'est une limite HTTP standard) : quand quelqu'un clique sur le
lien de réinitialisation reçu par email, Supabase le renvoie vers l'URL
demandée avec les identifiants dans le FRAGMENT de l'URL
(...#access_token=xxx&refresh_token=yyy&type=recovery). Le fragment (tout
ce qui suit le #) n'est JAMAIS envoyé au serveur par le navigateur -> le
backend Python de Streamlit ne peut absolument pas le lire via
st.query_params, quel que soit le code qu'on écrit côté Python. La seule
solution est un petit script qui s'exécute côté NAVIGATEUR, lit le
fragment, et redirige vers la même URL en recopiant ces valeurs dans la
query string (?access_token=xxx), que Streamlit peut alors lire
normalement au rechargement suivant.
"""

import os
import sys

import streamlit as st
import streamlit.components.v1 as components

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'core'))

from auth import etablir_session_depuis_tokens, mettre_a_jour_mot_de_passe  # noqa: E402


# window.parent.location (pas window.location) : ce script tourne dans
# l'iframe isolée que Streamlit utilise pour components.html, pas dans la
# page elle-même -> il faut viser explicitement la fenêtre parente pour
# lire/modifier la VRAIE barre d'adresse du navigateur.
_BRIDGE_JS = """
<script>
(function() {
    try {
        var loc = window.parent.location;
        if (loc.hash && loc.hash.indexOf("type=recovery") !== -1) {
            var params = new URLSearchParams(loc.hash.substring(1));
            var url = new URL(loc.href);
            url.hash = "";
            params.forEach(function(valeur, cle) { url.searchParams.set(cle, valeur); });
            loc.replace(url.toString());
        }
    } catch (e) {
        // Rien de bloquant si ça échoue (ex: restrictions navigateur) :
        // la personne pourra toujours redemander un lien.
    }
})();
</script>
"""


def gerer_recuperation_mot_de_passe():
    """
    À appeler tout en haut de la page, avant l'UI de connexion normale.

    Retourne True si un flux de réinitialisation est en cours (l'appelant
    doit alors faire `st.stop()` juste après, le formulaire "nouveau mot
    de passe" a déjà été affiché ici) ; False sinon (rien à faire,
    continuer le rendu normal de la page).
    """
    components.html(_BRIDGE_JS, height=0)

    query = st.query_params
    access_token = query.get("access_token")
    refresh_token = query.get("refresh_token")
    type_lien = query.get("type")

    if type_lien != "recovery" or not access_token or not refresh_token:
        return False

    st.markdown("### 🔑 Choisis un nouveau mot de passe")

    if "session_recuperation_etablie" not in st.session_state:
        succes, resultat = etablir_session_depuis_tokens(access_token, refresh_token)
        if not succes:
            st.error(resultat)
            return True
        st.session_state.session_recuperation_etablie = True

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
                # de la page relancerait ce même flux de récupération en boucle.
                st.query_params.clear()
                st.session_state.pop("session_recuperation_etablie", None)
            else:
                st.error(message)

    return True
