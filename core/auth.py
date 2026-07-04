"""
Authentification etudiant (email/mot de passe + Google), via Supabase Auth.

Le compte reste OPTIONNEL : le chat fonctionne sans connexion. On ne pousse
l'etudiant a se connecter que quand une fonctionnalite en a vraiment besoin
(ex: connecter son Notion plus tard).

POURQUOI UNE TABLE oauth_temp POUR GOOGLE :
Quand l'etudiant clique "Se connecter avec Google", il quitte entierement
l'app pour aller sur Google, puis revient. Ce depart/retour redemarre la
session Streamlit a zero (nouvelle session_state vide). Le "code_verifier"
PKCE genere avant le depart doit donc etre range quelque part qui survit a
ce redemarrage : la table oauth_temp (une ligne par tentative en cours,
supprimee juste apres usage).

NOTE TECHNIQUE : on construit nous-memes l'URL d'autorisation Google avec
les fonctions PKCE publiques de supabase-auth (generate_pkce_verifier,
generate_pkce_challenge) plutot que d'utiliser sign_in_with_oauth() de la
librairie. Raison : cette derniere stocke le code_verifier dans un espace
memoire interne au client, pense pour UN SEUL utilisateur a la fois (ex:
une appli mobile) - pas adapte a un backend qui sert plusieurs etudiants
en meme temps sur la meme instance.
"""

import os
import secrets
import logging
from urllib.parse import urlencode

from supabase import create_client
from supabase_auth.helpers import generate_pkce_verifier, generate_pkce_challenge


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
URL_RETOUR = get_secret("URL_RETOUR_APP")  # ex: https://telecom-ia.streamlit.app

if not SUPABASE_URL or not SUPABASE_SECRET:
    logging.error("SUPABASE_URL ou SUPABASE_SECRET manquant : l'authentification ne fonctionnera pas.")

if not URL_RETOUR:
    logging.warning(
        "URL_RETOUR_APP manquant dans les secrets : la connexion Google ne saura pas ou revenir. "
        "Ajoute par exemple URL_RETOUR_APP = \"https://tonapp.streamlit.app\""
    )

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)


def inscription(email, mot_de_passe):
    """
    Cree un compte etudiant par email/mot de passe.
    Retourne (succes: bool, message: str).
    """
    try:
        supabase.auth.sign_up({"email": email, "password": mot_de_passe})
        return True, "Compte cree. Verifie ta boite mail si une confirmation est demandee."
    except Exception as e:
        logging.error(f"ERREUR INSCRIPTION ({email}): {e}")
        return False, "Impossible de creer ce compte (email deja utilise ou mot de passe trop court)."


def connexion(email, mot_de_passe):
    """
    Connecte un etudiant deja inscrit par email/mot de passe.
    Retourne (succes: bool, session_ou_message).
    En cas de succes, le deuxieme element est la session Supabase
    (contient session.user.id, session.access_token, etc.).
    """
    try:
        resultat = supabase.auth.sign_in_with_password({"email": email, "password": mot_de_passe})
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR CONNEXION ({email}): {e}")
        return False, "Email ou mot de passe incorrect."


def demarrer_connexion_google():
    """
    Premiere etape de la connexion Google : genere l'URL vers laquelle
    rediriger l'etudiant, et range le code_verifier dans oauth_temp sous
    une cle aleatoire (state) qu'on retrouvera au retour.

    Retourne l'URL a ouvrir (str), ou None si la config manque.
    """
    if not URL_RETOUR:
        logging.error("Connexion Google impossible : URL_RETOUR_APP manquant.")
        return None

    state = secrets.token_urlsafe(24)
    code_verifier = generate_pkce_verifier()
    code_challenge = generate_pkce_challenge(code_verifier)

    try:
        supabase.table("oauth_temp").insert({
            "state": state,
            "code_verifier": code_verifier,
        }).execute()
    except Exception as e:
        logging.error(f"ERREUR ECRITURE oauth_temp : {e}")
        return None

    params = {
        "provider": "google",
        "redirect_to": URL_RETOUR,
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        # Notion/Supabase transmettent ce parametre tel quel dans l'URL de
        # retour : on s'en sert pour retrouver la bonne ligne oauth_temp.
        "state": state,
    }
    url_autorisation = f"{SUPABASE_URL}/auth/v1/authorize?{urlencode(params)}"
    return url_autorisation


def finaliser_connexion_google(code, state):
    """
    Deuxieme etape, appelee au retour de Google (l'app relit ?code=...&state=...
    dans l'URL et appelle cette fonction). Retrouve le code_verifier dans
    oauth_temp via state, termine l'echange, nettoie la table.

    Retourne (succes: bool, session_ou_message).
    """
    try:
        ligne = supabase.table("oauth_temp").select("code_verifier").eq("state", state).execute()
    except Exception as e:
        logging.error(f"ERREUR LECTURE oauth_temp : {e}")
        return False, "Connexion Google impossible (erreur interne)."

    if not ligne.data:
        return False, "Session de connexion expiree ou deja utilisee, reessaie."

    code_verifier = ligne.data[0]["code_verifier"]

    # Nettoyage immediat : une tentative = un usage, qu'elle reussisse ou non.
    try:
        supabase.table("oauth_temp").delete().eq("state", state).execute()
    except Exception as e:
        logging.warning(f"Nettoyage oauth_temp echoue (pas bloquant) : {e}")

    try:
        resultat = supabase.auth.exchange_code_for_session({
            "auth_code": code,
            "code_verifier": code_verifier,
        })
        return True, resultat.session
    except Exception as e:
        logging.error(f"ERREUR FINALISATION OAUTH GOOGLE : {e}")
        return False, "Connexion Google impossible (code invalide ou expire)."


def deconnexion():
    try:
        supabase.auth.sign_out()
    except Exception as e:
        logging.warning(f"ERREUR DECONNEXION (pas bloquant) : {e}")
