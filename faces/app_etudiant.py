"""
Point d'entrée unique de l'app (plateforme djiguigne).

Sépare deux univers totalement étanches, chacun avec sa propre navigation
construite via st.navigation() :

- Contexte "chat" : soit l'URL contient ?agent=xxx (lien d'un agent
  partagé), soit un secret AGENT_ID est configuré sur ce déploiement
  (anciens déploiements Streamlit Cloud mono-agent : tutorat-maths,
  telecom-ia). Dans ce contexte, seul le chat existe. "Créer un agent" et
  "Mes agents" n'apparaissent nulle part.
- Contexte "créateur" : ni ?agent= ni secret AGENT_ID (cas du déploiement
  Railway multi-agent, voir RAILWAY_DEPLOY.md qui recommande justement de
  NE PAS définir AGENT_ID dessus). Dans ce contexte, seuls "Créer un
  agent" et "Mes agents" existent. Le chat n'apparaît nulle part.

Pourquoi st.navigation() et pas le dossier faces/pages/ (auto-découvert
par Streamlit) : pages/ affiche TOUTES ses pages à TOUT visiteur, ce qui
mélangerait les deux univers dans la même barre de navigation (un
étudiant ouvrant un simple lien de chat verrait "Créer un agent" dans sa
sidebar, et inversement). Ici, un seul jeu de pages est construit selon
le contexte, décidé AVANT l'appel à st.navigation() -- d'où le déplacement
du contenu réel dans faces/vues/ (dossier ordinaire, non auto-découvert).
"""

import os
import streamlit as st


def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


try:
    _agent_depuis_url = st.query_params.get("agent")
except Exception:
    # st.query_params n'existe que sur des versions recentes de Streamlit.
    _agent_depuis_url = None

_agent_id_secret = get_secret("AGENT_ID")

if _agent_depuis_url or _agent_id_secret:
    # Contexte chat : une seule page, pas besoin d'afficher une barre de
    # navigation pour un choix unique.
    pages = [st.Page("vues/chat.py", title="Assistant", default=True)]
    navigation = st.navigation(pages, position="hidden")
else:
    # Contexte créateur : navigation entre les deux pages du tableau de
    # bord. "default=True" sur "Créer un agent" : c'est la page d'entrée
    # naturelle pour un tout premier visiteur sans compte ni agent.
    pages = [
        st.Page("vues/creer_agent.py", title="Créer un agent", icon="🧩", default=True),
        st.Page("vues/mes_agents.py", title="Mes agents", icon="📂"),
    ]
    navigation = st.navigation(pages)

navigation.run()
