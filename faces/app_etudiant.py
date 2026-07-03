"""
Face étudiant — interface Streamlit du coach mathématique.
"""

import sys
import os
import re
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))

import markdown as md_lib
import streamlit as st
import streamlit.components.v1 as components
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
""", unsafe_allow_html=True)


def _rendre_markdown(texte):
    """
    Le contenu est inséré dans un <div>, ce qui fait que Streamlit
    (CommonMark) bascule en mode "bloc HTML brut" et n'interprète plus
    aucun Markdown à l'intérieur (gras, listes, titres...). On convertit
    donc nous-mêmes le Markdown en HTML AVANT de l'insérer dans le div.

    Les segments $...$ / $$...$$ sont protégés pendant la conversion,
    car Markdown utilise aussi `_` et `*` pour l'italique/le gras, ce
    qui casserait des indices comme x_1 dans une formule.
    """
    maths = []

    def _extraire(m):
        maths.append(m.group(0))
        return f"@@MATH{len(maths) - 1}@@"

    texte_protege = re.sub(r'\$\$.*?\$\$|\$[^$\n]+?\$', _extraire, texte, flags=re.DOTALL)
    html_rendu = md_lib.markdown(texte_protege, extensions=['extra', 'sane_lists'])

    for i, formule in enumerate(maths):
        html_rendu = html_rendu.replace(f"@@MATH{i}@@", formule)

    return html_rendu


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


def _preparer_contenu(texte):
    """Normalise les délimiteurs LaTeX puis convertit le Markdown en HTML."""
    return _rendre_markdown(_normaliser_latex(texte))


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


if "messages" not in st.session_state:
    st.session_state.messages = []

if "compteur" not in st.session_state:
    st.session_state.compteur = 0


if len(st.session_state.messages) == 0:
    st.title("🎓 Votre coatch mathématique")
    st.caption("Tout comprendre sur les maths. Je te donne rien, je t'enseigne tout.")

for message in st.session_state.messages:
    if message["role"] == "user":
        st.markdown(f'<div class="message-user">{message["content"]}</div><div class="clearfix"></div>', unsafe_allow_html=True)
    else:
        contenu_affiche = _preparer_contenu(message["content"])
        st.markdown(f'<div class="message-assistant">{contenu_affiche}</div><div class="clearfix"></div>', unsafe_allow_html=True)

if prompt := st.chat_input("Pose ta question..."):
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
        contenu_affiche = _preparer_contenu(reponse_complete)
        placeholder.markdown(
            f'<div class="message-assistant">{contenu_affiche}🎓</div><div class="clearfix"></div>',
            unsafe_allow_html=True
        )

    contenu_affiche = _preparer_contenu(reponse_complete)
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
