"""
Constantes de thème partagées entre core/ (via faces/vues/chat.py, le rendu
réel) et faces/vues/creer_agent.py + faces/vues/mes_agents.py (les
formulaires). Un seul endroit pour la liste des polices/rayons/tailles
disponibles : sans ça, un choix affiché dans un formulaire pourrait ne
correspondre à rien côté rendu (ou inversement), au premier renommage
oublié d'un côté.

Compatibilité ascendante : entrepreneuriat et business (créés avant ce
fichier) ont déjà "police": "Lora (serif, actuelle)" enregistré en base.
Les alias tout en bas garantissent que ces libellés exacts continuent à
fonctionner sans changement de rendu, même si les nouveaux libellés
affichés dans le formulaire sont plus détaillés.
"""

# Police -> (segment d'URL Google Fonts, ou None si police système sans
# chargement externe ; pile CSS finale avec fallback générique).
POLICES = {
    "Lora (serif, chaleureux/pédagogique)": (
        "Lora:wght@400;500;600", "'Lora', serif"
    ),
    "Merriweather (serif, éditorial/sérieux)": (
        "Merriweather:wght@400;700", "'Merriweather', serif"
    ),
    "Poppins (sans-serif, moderne/amical)": (
        "Poppins:wght@400;500;600", "'Poppins', sans-serif"
    ),
    "Inter (sans-serif, neutre/professionnel)": (
        "Inter:wght@400;500;600", "'Inter', sans-serif"
    ),
    "Roboto Mono (monospace, technique/support informatique)": (
        "Roboto+Mono:wght@400;500", "'Roboto Mono', monospace"
    ),
    "Police système (sans-serif, chargement instantané, look natif de l'appareil)": (
        None, "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    ),
}

# Alias vers les libellés EXACTS utilisés par l'ancien formulaire (une seule
# police au choix entre ces deux strings). À ne jamais supprimer tant que
# des agents existants ont ces valeurs en base (voir docstring ci-dessus).
POLICES["Lora (serif, actuelle)"] = POLICES["Lora (serif, chaleureux/pédagogique)"]
POLICES["Police système (sans-serif)"] = POLICES["Police système (sans-serif, chargement instantané, look natif de l'appareil)"]

# Liste à afficher dans les formulaires (creer_agent.py/mes_agents.py) :
# les 6 vrais choix, dans l'ordre, sans les alias de compatibilité
# ci-dessus (qui ne doivent jamais apparaître comme une NOUVELLE option
# proposée à un créateur, seulement rester compris si déjà en base).
POLICES_AFFICHEES = [
    "Lora (serif, chaleureux/pédagogique)",
    "Merriweather (serif, éditorial/sérieux)",
    "Poppins (sans-serif, moderne/amical)",
    "Inter (sans-serif, neutre/professionnel)",
    "Roboto Mono (monospace, technique/support informatique)",
    "Police système (sans-serif, chargement instantané, look natif de l'appareil)",
]

POLICE_PAR_DEFAUT = "Lora (serif, chaleureux/pédagogique)"


def police_vers_css(label_police):
    """
    Retourne (import_google_fonts_ou_vide, pile_css) pour un libellé donné.
    Retombe sur Lora (comportement historique) si le libellé est inconnu
    (ex: valeur jamais enregistrée, ou corrompue) : ne casse jamais le
    rendu, dans le pire cas l'agent affiche juste une police différente
    de celle voulue plutôt qu'une erreur.
    """
    google_font, pile_css = POLICES.get(label_police, POLICES[POLICE_PAR_DEFAUT])
    import_css = (
        f"@import url('https://fonts.googleapis.com/css2?family={google_font}&display=swap');"
        if google_font else ""
    )
    return import_css, pile_css


# Arrondi des bulles de message. "18px" = valeur historique codée en dur
# avant l'ajout de ce réglage (voir chat.py) : reste le défaut pour ne
# rien changer aux agents déjà créés qui n'ont pas cette clé.
RAYONS = {
    "Carré (0px) — sobre, institutionnel": "0px",
    "Léger (8px) — discret": "8px",
    "Arrondi (18px) — actuel, chaleureux": "18px",
    "Très arrondi (28px) — ludique, façon bulle de SMS": "28px",
}
RAYON_PAR_DEFAUT = "Arrondi (18px) — actuel, chaleureux"

# Taille du texte des réponses de l'agent.
TAILLES = {
    "Compact (14px) — dense, plus de texte visible à l'écran": "14px",
    "Normal (16px) — équilibre lisibilité/densité": "16px",
    "Grand (18px) — plus lisible, utile pour un public âgé ou malvoyant": "18px",
}
TAILLE_PAR_DEFAUT = "Normal (16px) — équilibre lisibilité/densité"
