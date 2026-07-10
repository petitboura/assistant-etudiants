"""
Face publique — Vitrine Djiguignè AI (plateforme djiguigne).

Nouvelle page d'entrée du contexte "créateur" (voir faces/app_etudiant.py).
Contrairement à creer_agent.py / mes_agents.py, cette page n'a AUCUNE
logique métier : ni Supabase, ni auth, ni formulaire. Son seul rôle est de
présenter la marque et de rediriger vers les deux pages fonctionnelles
existantes via st.switch_page — donc pas de risque de casser quoi que ce
soit côté données en la modifiant plus tard.
"""

import streamlit as st

from theme_djiguigne import injecter_theme, afficher_logo_hero

st.set_page_config(
    page_title="Djiguignè AI — Assistants IA prêts à l'emploi",
    page_icon="🟠",
    layout="centered",
)
injecter_theme()

afficher_logo_hero(taille=124)

st.markdown(
    """
    <div class="dj-hero-wrap" style="text-align:center; margin-top:0.4rem;">
        <h1 class="dj-display" style="font-size:2.6rem; line-height:1.1; margin-bottom:0.7rem;">
            Djiguignè <span style="color:var(--dj-accent-1);">AI</span>
        </h1>
        <p style="color:var(--dj-texte-muet); font-size:1.12rem; max-width:520px; margin:0 auto 0.4rem auto;">
            Crée un assistant IA sur-mesure — connecté à ta base de connaissance,
            à tes outils, à ta marque — et partage-le en un lien. Pas de code,
            pas de déploiement.
        </p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")
col_espace_g, col_cta_1, col_cta_2, col_espace_d = st.columns([1, 1.3, 1.3, 1])
with col_cta_1:
    if st.button("Se connecter", use_container_width=True, key="cta_connexion"):
        st.switch_page("vues/creer_agent.py")
with col_cta_2:
    if st.button("Créer un compte", use_container_width=True, key="cta_inscription"):
        st.switch_page("vues/creer_agent.py")

st.write("")
st.write("")

# --- Bandeau de fonctionnalités -----------------------------------------
_fonctionnalites = [
    (
        "🧠",
        "Un agent, une identité",
        "Ton assistant apprend son ton, ses limites et son domaine — "
        "pas un chatbot générique, le tien.",
    ),
    (
        "📚",
        "Connecté à ta base",
        "PDF, texte, ou pages Notion : indexés automatiquement, "
        "consultés par recherche sémantique à chaque réponse.",
    ),
    (
        "🔗",
        "En ligne en un lien",
        "Aucun déploiement de ton côté — l'agent est accessible "
        "immédiatement, à partager tel quel.",
    ),
]

st.markdown(
    '<h3 class="dj-display" style="text-align:center; font-size:1.3rem; '
    'color:var(--dj-texte-muet); font-weight:600; margin-bottom:1.2rem;">'
    "Ce que tu peux faire</h3>",
    unsafe_allow_html=True,
)

cols = st.columns(3)
for i, (emoji, titre, texte) in enumerate(_fonctionnalites):
    with cols[i]:
        st.markdown(
            f"""
            <div style="background:var(--dj-surface); border:1px solid var(--dj-bordure);
                        border-radius:16px; padding:1.3rem 1.1rem; height:190px;
                        animation: dj-fade-up 0.6s ease both; animation-delay:{i * 0.1}s;
                        transition: border-color 0.2s ease, transform 0.2s ease;">
                <div style="font-size:1.6rem; margin-bottom:0.6rem;">{emoji}</div>
                <div style="font-family:'Bricolage Grotesque',sans-serif; font-weight:700;
                            font-size:1rem; margin-bottom:0.4rem; color:var(--dj-texte);">{titre}</div>
                <div style="color:var(--dj-texte-muet); font-size:0.86rem; line-height:1.45;">{texte}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.write("")
st.write("")
st.markdown(
    """
    <div style="text-align:center; color:var(--dj-texte-muet); font-size:0.8rem;
                font-family:'JetBrains Mono',monospace; opacity:0.7; margin-top:1rem;">
        Djiguignè AI — un projet Maame
    </div>
    """,
    unsafe_allow_html=True,
)
