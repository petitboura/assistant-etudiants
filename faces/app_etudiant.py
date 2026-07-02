"""
Face étudiant — interface Streamlit du coach mathématique.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))

import streamlit as st
from main import chat

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
    </style>

    <script>
    window.MathJax = {tex: {inlineMath: [['$', '$']]}, svg: {fontCache: 'global'}};
    </script>
    <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
""", unsafe_allow_html=True)

if "messages" not in st.session_state:
    st.session_state.messages = []

if "compteur" not in st.session_state:
    st.session_state.compteur = 0

LIMITE = 5

if len(st.session_state.messages) == 0:
    st.title("🎓 Votre coatch mathématique")
    st.caption("Tout comprendre sur les maths. Je te donne rien, je t'enseigne tout.")

for message in st.session_state.messages:
    if message["role"] == "user":
        st.markdown(f'<div class="message-user">{message["content"]}</div><div class="clearfix"></div>', unsafe_allow_html=True)
    else:
        st.markdown(f'<div class="message-assistant">{message["content"]}</div><div class="clearfix"></div>', unsafe_allow_html=True)

if st.session_state.compteur >= LIMITE:
    st.warning("Désolé, nous sommes actuellement en phase de test et devons limiter nos coûts. Votre session est donc temporairement restreinte. Ne vous inquiétez pas : dès le lancement officiel de l'application, vous bénéficierez d'un accès complet sans limitation.")
    st.markdown("---")
    st.markdown("Ton avis compte, dis-nous ce que tu penses !")
    st.link_button("Remplir le formulaire", "https://forms.gle/zQPQsb9cX46188oh9")

elif prompt := st.chat_input("Pose ta question..."):
    st.session_state.compteur += 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.markdown(f'<div class="message-user">{prompt}</div><div class="clearfix"></div>', unsafe_allow_html=True)

    historique = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]

    placeholder = st.empty()
    reponse_complete = ""

    for token in chat(prompt, historique):
        reponse_complete += token
        placeholder.markdown(
            f'<div class="message-assistant">{reponse_complete}🎓</div><div class="clearfix"></div>',
            unsafe_allow_html=True
        )

    placeholder.markdown(
        f'<div class="message-assistant">{reponse_complete}</div><div class="clearfix"></div>',
        unsafe_allow_html=True
    )

    st.session_state.messages.append({"role": "assistant", "content": reponse_complete})

if st.session_state.compteur >= 3:
    st.markdown("---")
    st.markdown("Ton avis compte, dis-nous ce que tu penses !")
    st.link_button("Remplir le formulaire", "https://forms.gle/zQPQsb9cX46188oh9")
