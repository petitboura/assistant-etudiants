import streamlit as st
import time
from main import chat

st.set_page_config(page_title="Votre coatch mathématique", page_icon="🎓", layout="centered")

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600&display=swap');
    
    .message-user {
        background-color: rgba(0, 0, 0, 0.3);
        color: white;
        padding: 12px 18px;
        border-radius: 18px;
        margin: 8px 0;
        max-width: 75%;
        margin-left: auto;
        text-align: right;
        border: 1px solid rgba(255,255,255,0.1);
    }
    
    .message-assistant {
        font-family: 'Lora', serif;
        color: #e5e5e5;
        padding: 10px 4px;
        margin: 8px 0;
        max-width: 85%;
        line-height: 1.7;
    }
    
    .clearfix { clear: both; }
    </style>
    
    <script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script>
    <script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
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

reflexions = [
    "Analyse de votre question...",
    "Recherche dans les cours...",
    "Formulation de la réponse...",
    "Rédaction en cours..."
]

if st.session_state.compteur >= LIMITE:
    st.warning("Désolé, nous sommes actuellement en phase de test et devons limiter nos coûts. Votre session est donc temporairement restreinte. Ne vous inquiétez pas : dès le lancement officiel de l'application, vous bénéficierez d'un accès complet sans limitation.")
    st.markdown("---")
    st.markdown("**💬 Donne-nous ton avis !**")
    st.link_button("📝 Remplir le formulaire de feedback", "https://forms.gle/zQPQsb9cX46188oh9")
elif prompt := st.chat_input("Pose ta question..."):
    st.session_state.compteur += 1
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.markdown(f'<div class="message-user">{prompt}</div><div class="clearfix"></div>', unsafe_allow_html=True)

    placeholder = st.empty()
    for msg in reflexions:
        placeholder.markdown(f"*{msg}*")
        time.sleep(2)
    
    response = chat(prompt)
    placeholder.empty()

    st.markdown(f'<div class="message-assistant">{response}</div><div class="clearfix"></div>', unsafe_allow_html=True)
    st.session_state.messages.append({"role": "assistant", "content": response})

# Bouton formulaire toujours visible en bas
st.markdown("---")
st.markdown("**💬 Tu as 2 minutes ? Donne-nous ton avis !**")
st.link_button("📝 Remplir le formulaire de feedback", "https://forms.gle/zQPQsb9cX46188oh9")