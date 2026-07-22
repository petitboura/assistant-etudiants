import os
import json
import logging
import base64
import re
import concurrent.futures
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from groq import Groq
from google import genai
from google.genai import types
from supabase import create_client
from configuration import get_system_prompt
from retriever import chercher_candidats
from mcp_tools import lister_tous_les_outils, appeler_outil
from registre_outils import OUTILS_SENSIBLES

logging.basicConfig(level=logging.INFO)


def get_secret(key):
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        return os.environ.get(key)


SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_SECRET = get_secret("SUPABASE_SECRET")
supabase = create_client(SUPABASE_URL, SUPABASE_SECRET)

GROQ_PRIMARY = "openai/gpt-oss-120b"
GOOGLE_MODEL = "gemini-2.5-flash"
GROQ_FALLBACKS = [
    "llama-3.3-70b-versatile",
    "qwen/qwen3.6-27b",
    "openai/gpt-oss-20b",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    # qwen3-32b a une limite de 6000 tokens/minute, souvent plus petite que
    # la taille du prompt systeme + historique a elle seule (avant meme un
    # appel d'outil) -> il echoue quasi systematiquement (413). On le garde
    # en tout dernier recours plutot qu'en premier, pour ne pas gaspiller
    # un aller-retour a chaque question.
    "qwen/qwen3-32b",
]
MESSAGE_ERREUR = "Désolé, je rencontre un souci technique pour répondre. Merci de réessayer dans un instant."

# Valeur de repli si le secret AGENT_ID n'est pas defini pour ce deploiement
# (doit rester alignee avec AGENT_ID_PAR_DEFAUT dans retriever.py).
AGENT_ID_PAR_DEFAUT = "tutorat-maths"

# Au-dela de ce nombre de messages non resumes (table conversations), on
# redemande un resume condense au modele plutot que d'empiler indefiniment
# l'historique brut dans conversation_summaries.
SEUIL_RESUME_MESSAGES = 20
MODELE_RESUME = "llama-3.3-70b-versatile"  # rapide, pas besoin de raisonnement pour resumer

# D'apres la doc Groq (console.groq.com/docs/reasoning), le parametre
# reasoning_effort n'est reconnu que par certains modeles (GPT-OSS 20B/120B,
# Qwen 3). Les autres modeles de GROQ_FALLBACKS (ex: llama-3.3-70b-versatile,
# llama-4-scout) ne sont PAS des modeles de raisonnement : leur envoyer ce
# parametre risque une erreur API plutot qu'un simple no-op. On ne l'active
# donc que pour les modeles confirmes compatibles.
MODELES_AVEC_REASONING_EFFORT = {
    "openai/gpt-oss-20b",
    "openai/gpt-oss-120b",
    "qwen/qwen3-32b",
    "qwen/qwen3.6-27b",  # successeur de qwen3-32b, a confirmer si le comportement differe
}

# Nombre maximum d'aller-retours "outil" autorisés pour une seule question,
# pour éviter qu'un modèle ne boucle indéfiniment sur le même outil.
MAX_ETAPES_OUTILS = 5

# Noms lisibles affichés à l'utilisateur pendant qu'un outil MCP est utilisé.
# Nouvel outil = ajouter une ligne ici (optionnel, sinon le nom brut s'affiche).
NOMS_OUTILS_LISIBLES = {
    "tavily_search": "Recherche sur le web",
    "tavily_extract": "Lecture d'une page web",
    "tavily_crawl": "Exploration d'un site web",
    "tavily_map": "Cartographie d'un site web",
    "tavily_research": "Recherche approfondie",
    "notion-search": "Recherche dans ton Notion",
    "notion-fetch": "Lecture d'une page Notion",
    "notion-create-pages": "Création d'une page Notion",
    "notion-update-page": "Modification d'une page Notion",
}


def _construire_parts_gemini(texte, images=None):
    """
    Construit la liste `parts` d'un message Gemini. Le texte est toujours
    présent ; `images` (si fourni) est une liste de tuples
    (bytes, mime_type), ajoutés en inline_data base64 -- format REST
    attendu par google-genai pour du contenu multimodal, voir
    https://ai.google.dev/gemini-api/docs/vision. Une image (cas simple)
    ou plusieurs (frames vidéo, voir _extraire_frames_video) sont traitées
    de la même façon.
    """
    parts = [{"text": texte}]
    for image_bytes, image_mime in (images or []):
        parts.append({
            "inline_data": {
                "mime_type": image_mime or "image/jpeg",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
            }
        })
    return parts


def _telecharger_image(image_url):
    """
    Télécharge l'image pointée par `image_url` (URL publique Supabase
    Storage, voir api/uploads.py:uploader_image_chat) pour l'envoyer en
    base64 à Gemini. On ne passe jamais l'URL telle quelle à Gemini : les
    URLs Supabase ne sont pas des URI Google Cloud Storage, `Part.from_uri`
    ne les accepterait pas.
    """
    reponse = requests.get(image_url, timeout=15)
    reponse.raise_for_status()
    return reponse.content, reponse.headers.get("content-type", "image/jpeg")


REGEX_URL = re.compile(r"https?://[^\s<>\"']+")
LONGUEUR_MAX_TEXTE_URL = 8_000  # caracteres, par lien, pour ne pas saturer le prompt


def _extraire_id_youtube(url):
    match = re.search(r"(?:youtu\.be/|youtube\.com/watch\?v=|youtube\.com/shorts/)([\w-]{11})", url)
    return match.group(1) if match else None


def _lire_url(url):
    """
    Récupère le contenu textuel d'un lien collé dans le message. Deux cas :
    - YouTube (vidéo) : transcript via youtube-transcript-api, pas de
      scraping HTML -- c'est notre seule "entrée vidéo" pour l'instant,
      limitée aux vidéos YouTube sous-titrées (voir note plus bas, pas de
      vrai traitement vidéo/image par frame).
    - Page web générique : extraction via trafilatura (garde le texte
      utile, jette nav/pubs/footer).
    Retourne None si l'extraction échoue (lien mort, page protégée, vidéo
    sans sous-titres...) -- on ne bloque jamais le message pour ça, on
    l'envoie tel quel au modèle.
    """
    id_youtube = _extraire_id_youtube(url)
    if id_youtube:
        try:
            # BUG corrigé le 2026-07-20 : même famille de bug que
            # trafilatura.fetch_url(timeout=...) -- youtube-transcript-api
            # 1.x a totalement changé son API par rapport à l'ancienne
            # version que j'avais en tête. `YouTubeTranscriptApi.get_transcript`
            # (méthode statique, résultat = liste de dicts) n'existe plus :
            # il faut instancier la classe et appeler `.fetch()` (méthode
            # d'instance), qui renvoie un objet FetchedTranscript itérable
            # de FetchedTranscriptSnippet (dataclasses avec un attribut
            # `.text`, pas une clé de dict `["text"]`). Confirmé cassé en
            # test réel le 2026-07-20 (lien YouTube collé, aucun contenu
            # récupéré, le modèle répondait qu'il ne pouvait pas voir de
            # vidéos -- comme pour trafilatura, l'exception était avalée
            # silencieusement par le except plus bas).
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            transcript = api.fetch(id_youtube, languages=["fr", "en"])
            texte = " ".join(morceau.text for morceau in transcript)
            return texte[:LONGUEUR_MAX_TEXTE_URL]
        except Exception as e:
            logging.error(f"ERREUR TRANSCRIPT YOUTUBE ({url}): {e}")
            return None

    try:
        import trafilatura
        # BUG corrigé le 2026-07-20 : trafilatura 2.1.0 n'a pas de paramètre
        # `timeout` sur fetch_url() (TypeError à CHAQUE appel, silencieux
        # car avalé par le except plus bas -- résultat : cette fonction ne
        # récupérait jamais aucun lien depuis le déploiement initial,
        # confirmé en testant en conditions réelles contre Wikipedia et
        # ia-info.fr, qui échouaient identiquement). Le timeout par défaut
        # de trafilatura reste raisonnable, pas besoin de le personnaliser.
        telechargement = trafilatura.fetch_url(url)
        if not telechargement:
            # Échec SILENCIEUX auparavant (aucun log) -- cas exact vécu le
            # 2026-07-20 : impossible de distinguer depuis les logs si le
            # lien a été bloqué (ex: 429, comme YouTube l'a fait à Claude
            # directement lors du diagnostic), jamais tenté, ou un autre
            # souci. trafilatura n'expose pas le code HTTP ici (fetch_url
            # avale l'erreur en interne), donc on log au moins le fait
            # qu'un téléchargement a été tenté et a échoué.
            logging.warning(f"LECTURE URL ECHOUEE (telechargement vide, ex: bloqué/429/timeout) : {url}")
            return None
        texte = trafilatura.extract(telechargement)
        if not texte:
            logging.warning(f"LECTURE URL ECHOUEE (page téléchargée mais aucun texte extrait, ex: page vide/JS-only) : {url}")
            return None
        return texte[:LONGUEUR_MAX_TEXTE_URL]
    except Exception as e:
        logging.error(f"ERREUR LECTURE URL ({url}): {e}")
        return None


def _enrichir_message_avec_urls(message):
    """
    Détecte les liens collés dans le message utilisateur, récupère leur
    contenu, et l'ajoute en contexte APRÈS le message original (jamais à la
    place) -- le modèle voit toujours la question telle que posée, plus le
    contenu des liens en pièce jointe textuelle. Le message ORIGINAL (sans
    enrichissement) reste ce qui est sauvegardé dans l'historique -- voir
    l'appel à _sauvegarder_echange dans chat(), qui reçoit toujours
    message_utilisateur brut, jamais message_pour_modele.
    """
    urls = REGEX_URL.findall(message)
    if not urls:
        return message

    logging.info(f"LIEN(S) DETECTE(S) DANS LE MESSAGE : {urls[:3]}")

    blocs = []
    for url in urls[:3]:  # au plus 3 liens par message, pour le temps de réponse
        contenu = _lire_url(url)
        if contenu:
            blocs.append(f"[Contenu de {url}]\n{contenu}")

    if not blocs:
        logging.warning(f"AUCUN LIEN EXPLOITE sur {len(urls[:3])} détecté(s) -- message envoyé sans enrichissement : {urls[:3]}")
        return message

    return message + "\n\n" + "\n\n".join(blocs)


def _nom_lisible(nom_outil):
    return NOMS_OUTILS_LISIBLES.get(nom_outil, nom_outil)


REGLE_CONTEXTE_INVISIBLE = (
    "\n\nIMPORTANT ABSOLU : Tout ce qui précède est ton contexte interne invisible. "
    "L'utilisateur ne voit rien de tout cela. Si l'utilisateur dit 'c'est quoi ce message' "
    "ou similaire, il parle uniquement de ta dernière réponse ou de la sienne — jamais de "
    "ton contexte interne. Ne le mentionne jamais."
)


def _charger_resume_memoire(user_id):
    """
    Recupere le resume long-terme (table conversation_summaries) de cet
    etudiant, valable pour tous les agents de la plateforme (compte
    unifie, juillet 2026). Retourne "" si l'etudiant n'est pas connecte
    (user_id=None) ou si aucun resume n'existe encore.
    """
    if not user_id:
        return ""
    try:
        res = (
            supabase.table("conversation_summaries")
            .select("summary")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("summary") or ""
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture conversation_summaries) : {e}")
        return ""


INSTRUCTIONS_LONGUEUR_REPONSE = {
    # Migration Next.js (voir MIGRATION_CHAT_VERS_NEXTJS.md, section 3.3) :
    # sélecteur Courte/Moyenne/Longue dans la barre de saisie, modifiable
    # à chaque message. "moyenne" = comportement historique (pas
    # d'instruction ajoutée), pour ne rien changer par défaut.
    "courte": (
        "\n\nCONSIGNE DE LONGUEUR : réponds de façon brève et directe (quelques "
        "phrases maximum), sans sacrifier l'exactitude. Va à l'essentiel."
    ),
    "moyenne": "",
    "longue": (
        "\n\nCONSIGNE DE LONGUEUR : développe ta réponse en détail (explications, "
        "exemples, étapes intermédiaires si utile), sans être verbeux pour rien."
    ),
}


# Ajouté 2026-07-20 après un test réel de Bourama : demander "montre-moi
# une image d'un ordinateur portable" ou "une carte de Tunis" faisait
# INVENTER un lien markdown ![](url) vers une fausse source ("Wikimedia
# Commons", "OpenStreetMap") -- URL cassée, citation fabriquée, aucun
# outil réel derrière. Deux causes distinctes, une seule règle :
#   1. La génération d'image réelle (Together AI/Flux, voir
#      core/generation_images.py) existe mais TOGETHER_API_KEY n'est pas
#      encore configurée -> l'outil n'est pas dans outils_mcp, donc
#      injoignable. Pas de solution ici tant que la clé n'est pas ajoutée.
#   2. Carte/graphique/widget interactif N'ONT JAMAIS eu d'outil dédié --
#      le frontend (djiguign--ai) sait déjà rendre ces trois blocs
#      nativement (voir CarteMessage.tsx, GraphiqueDonnees.tsx,
#      WidgetSandbox.tsx), il manquait juste la convention ici.
INSTRUCTIONS_FORMATS_AFFICHAGE = (
    "\n\nFORMATS ENRICHIS DISPONIBLES : l'interface sait afficher nativement les "
    "blocs suivants (à utiliser quand ils apportent une vraie valeur, jamais pour "
    "décorer) :\n"
    "- ```mermaid ... ``` pour un diagramme (flowchart, séquence, état...).\n"
    "- ```chart avec un JSON {\"type\": \"line\"|\"bar\"|\"pie\", \"data\": [...], "
    "\"titre\"?: \"...\"} pour un graphique. \"data\" est un tableau d'objets plats ; "
    "la première clé sert d'axe X (ou de nom pour \"pie\"), les suivantes sont les "
    "séries.\n"
    "- ```carte avec un JSON {\"lat\": ..., \"lng\": ..., \"label\"?: \"...\"} pour "
    "localiser un lieu (tu connais les coordonnées des lieux courants).\n"
    "- ```widget ou ```html avec du HTML/CSS/JS complet et autonome pour un mini-outil "
    "interactif (calculateur, formulaire, mini-jeu). Le fond est déjà sombre par défaut "
    "(assorti au reste de l'interface) : ne redéfinis PAS un fond clair/blanc pour tout "
    "le widget sauf besoin réel, et si tu le fais, redéfinis AUSSI la couleur du texte en "
    "conséquence -- sinon le texte clair hérité du thème sombre devient illisible sur fond "
    "clair (repéré en test réel : carte blanche avec texte quasi invisible).\n"
    "\n"
    "LÉGER (blocs ci-dessus) VS RÉEL (outils de génération, si disponibles dans "
    "cette conversation) : les deux existent pour des besoins différents, choisis "
    "selon ce que la situation demande réellement, jamais par défaut vers l'un ou "
    "l'autre. Un bloc ci-dessus est un aperçu léger affiché directement dans la "
    "conversation (une carte, un graphique, un petit outil interactif) -- utilise-le "
    "quand la personne veut voir/comprendre quelque chose tout de suite, sans notion "
    "de fichier. Un outil de génération produit un livrable réel, téléchargeable ou "
    "partageable (document à envoyer, site à héberger, image/audio/vidéo/modèle 3D "
    "en tant que fichier) -- réserve-le aux cas où un vrai fichier autonome a du sens "
    "pour l'usage décrit, pas chaque fois qu'un des deux pourrait techniquement "
    "répondre à la demande. Dans le doute, le format le plus léger qui répond à la "
    "demande est le bon choix.\n"
    "RÈGLE ABSOLUE, sans exception : n'utilise JAMAIS ![alt](url) (image markdown) "
    "avec une URL que tu inventes ou que tu crois plausible sans l'avoir obtenue "
    "d'un outil réel dans cette conversation. N'invente jamais non plus une "
    "attribution de source (\"Source : Wikimedia Commons\", \"via OpenStreetMap\"...) "
    "pour un contenu que tu n'as pas réellement obtenu. Si on te demande une image et "
    "qu'aucun outil de génération d'image n'est disponible dans cette conversation, "
    "dis-le clairement plutôt que d'inventer un lien.\n"
    "À L'INVERSE, dès qu'un outil de génération (image, document, code, site, "
    "bundle, données, audio, vidéo, 3D...) te renvoie une URL réelle, tu DOIS "
    "l'inclure dans ta réponse, sans exception : ![description](url) pour une "
    "image, ou [nom du fichier](url) pour tout autre type de fichier. Ne décris "
    "jamais un résultat généré sans donner le lien correspondant."
)


def _construire_system_prompt(message_utilisateur, agent_id, user_id=None, longueur_reponse="moyenne", fuseau_horaire=None):
    system_prompt = get_system_prompt(agent_id)
    candidats = chercher_candidats(message_utilisateur, agent_id=agent_id)
    resume_memoire = _charger_resume_memoire(user_id)

    instructions = "".join(f"\n{c['contenu']}\n" for c in candidats.get("prompts", []))
    contexte_docs = "".join(f"\n{c['contenu']}\n" for c in candidats.get("documents", []))

    system_final = system_prompt
    if resume_memoire:
        system_final += (
            "\n\nCONTEXTE DES SESSIONS PRÉCÉDENTES AVEC CET ÉTUDIANT (résumé, à utiliser "
            f"pour personnaliser ta réponse, ne jamais le réciter tel quel) :\n{resume_memoire}"
        )
    if instructions:
        system_final += f"\n\n{instructions}"
    if contexte_docs:
        system_final += f"\n\n{contexte_docs}"
    system_final += INSTRUCTIONS_LONGUEUR_REPONSE.get(longueur_reponse, "")
    system_final += INSTRUCTIONS_FORMATS_AFFICHAGE
    system_final += REGLE_CONTEXTE_INVISIBLE

    # Contexte système "date/heure actuelle" (2026-07-20) : sans ça, le
    # modèle ne sait pas qu'on est en 2026 et peut situer les événements
    # récents n'importe où par rapport à sa coupure d'entraînement.
    #
    # Fuseau horaire (corrigé 2026-07-20) : PAS figé sur Tunis -- Djiguignè
    # est un projet panafricain (voir Maame), rien ne dit que l'étudiant
    # est à Tunis. `fuseau_horaire` vient du navigateur
    # (Intl.DateTimeFormat().resolvedOptions().timeZone, voir
    # ChatIA.tsx:envoyerMessage), pas d'une valeur choisie côté serveur.
    # Repli sur UTC si absent ou si le navigateur envoie un nom de fuseau
    # invalide (ZoneInfo lève ZoneInfoNotFoundError) -- jamais une supposition
    # de pays.
    try:
        fuseau = ZoneInfo(fuseau_horaire) if fuseau_horaire else ZoneInfo("UTC")
    except Exception:
        fuseau = ZoneInfo("UTC")
    maintenant = datetime.now(fuseau)
    jours_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois_fr = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    date_fr = f"{jours_fr[maintenant.weekday()]} {maintenant.day} {mois_fr[maintenant.month - 1]} {maintenant.year}, {maintenant.strftime('%H:%M')}"
    system_final += f"\n\nNous sommes le {date_fr} (fuseau : {fuseau.key if hasattr(fuseau, 'key') else 'UTC'})."

    logging.info(
        f"Prompt système construit -> base_notion:{len(system_prompt)} caractères, "
        f"memoire:{'oui' if resume_memoire else 'NON'}, "
        f"instructions:{'oui' if instructions else 'NON'}, "
        f"contexte_docs:{'oui' if contexte_docs else 'NON'}"
    )
    return system_final


def _est_timeout(erreur):
    return "timeout" in str(erreur).lower()


DELAI_MAX_PAR_APPEL = 10  # secondes : on bascule vite plutot que d'attendre
MAX_PASSAGES_CASCADE = 2  # on ne retente toute la cascade que si TOUT a timeout


def _sauvegarder_echange(user_id, agent_id, message_utilisateur, reponse_finale, conversation_id=None):
    """
    Persiste l'echange (question + reponse) dans `conversations`, pour la
    memoire long-terme. Ignore silencieusement si l'etudiant n'est pas
    connecte (user_id=None) ou si la reponse est vide (ex: message
    d'erreur technique, qu'on ne veut pas polluer la memoire avec).
    """
    ids_historique = None  # renvoyé à l'appelant pour l'indexation du feedback

    if not user_id or not (reponse_finale or "").strip():
        return ids_historique
    try:
        supabase.table("conversations").insert([
            {"user_id": user_id, "agent_id": agent_id, "role": "user", "content": message_utilisateur},
            {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": reponse_finale},
        ]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sauvegarde conversations) : {e}")

    # Ajouté le 2026-07-13 (Bourama : historique de conversation visible,
    # conservée par agent, dans le tableau de bord). Table SÉPARÉE de
    # `conversations` ci-dessus, jamais purgée -- voir le commentaire de
    # migration (historique_conversations) pour le detail de la
    # distinction. Volontairement dans un bloc try/except À PART : si cette
    # écriture échoue, ça ne doit jamais faire échouer la mémoire de l'IA
    # ci-dessus, qui est la partie critique pour la qualité des réponses.
    #
    # `conversation_id` (2026-07-13, Bourama : liste de conversations
    # distinctes et cliquables dans la sidebar de chat.py, façon Claude.ai)
    # regroupe les messages d'un même fil de discussion, généré côté
    # chat.py (une valeur par conversation affichée, PAS par message) et
    # simplement transmis ici tel quel. None accepté (colonne nullable) :
    # un appelant qui ne gère pas encore les fils continue de fonctionner
    # sans erreur, ses messages sont juste groupés sous "historique ancien"
    # côté affichage plutôt que dans un fil précis.
    try:
        res = (
            supabase.table("historique_conversations")
            .insert([
                {"user_id": user_id, "agent_id": agent_id, "role": "user", "content": message_utilisateur, "conversation_id": conversation_id},
                {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": reponse_finale, "conversation_id": conversation_id},
            ])
            .execute()
        )
        lignes = res.data or []
        ligne_user = next((l for l in lignes if l["role"] == "user"), None)
        ligne_assistant = next((l for l in lignes if l["role"] == "assistant"), None)
        if ligne_user and ligne_assistant:
            ids_historique = {
                "message_id_user": ligne_user["id"],
                "message_id_assistant": ligne_assistant["id"],
                "created_at_assistant": ligne_assistant.get("created_at"),
            }
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sauvegarde historique_conversations) : {e}")

    return ids_historique


def _mettre_a_jour_resume_si_besoin(user_id):
    """
    Si assez de nouveaux messages bruts se sont accumules (>= SEUIL_RESUME_MESSAGES)
    depuis le dernier resume, en regenere un condense (ancien resume + messages
    recents) via un modele Groq rapide, l'ecrit dans conversation_summaries, puis
    purge les messages bruts desormais condenses. Ne bloque jamais la reponse a
    l'etudiant : toute erreur est juste loguee, jamais remontee a l'appelant.

    Compte unifie (juillet 2026) : scope par user_id seul, tous agents
    confondus. `agent_id` reste present dans `conversations` en tant que
    simple metadonnee de tracabilite (colonne non retiree par la
    migration), mais ne filtre plus rien ici -> les messages de tous les
    agents de la plateforme alimentent le meme resume.
    """
    if not user_id:
        return
    try:
        messages = (
            supabase.table("conversations")
            .select("id, role, content, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(SEUIL_RESUME_MESSAGES)
            .execute()
        ).data or []

        if len(messages) < SEUIL_RESUME_MESSAGES:
            return  # pas encore assez de matiere pour justifier un resume

        ancien_resume = _charger_resume_memoire(user_id)
        messages_recents = "\n".join(
            f"{'Étudiant' if m['role'] == 'user' else 'Assistant'} : {m['content']}"
            for m in reversed(messages)
        )

        prompt_resume = (
            "Condense ce qui suit en un résumé factuel et concis (5-8 lignes maximum) "
            "du profil et de la progression de cet étudiant : ses sujets de difficulté "
            "récurrents, son niveau apparent, les méthodes qui ont fonctionné pour lui. "
            "Pas de politesse, pas de méta-commentaire, juste les faits utiles pour "
            "personnaliser une future session.\n\n"
        )
        if ancien_resume:
            prompt_resume += f"Résumé précédent :\n{ancien_resume}\n\n"
        prompt_resume += f"Nouveaux échanges à intégrer :\n{messages_recents}"

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
        completion = client_groq.chat.completions.create(
            model=MODELE_RESUME,
            messages=[{"role": "user", "content": prompt_resume}],
            max_completion_tokens=None,
            timeout=DELAI_MAX_PAR_APPEL,
        )
        nouveau_resume = completion.choices[0].message.content.strip()

        supabase.table("conversation_summaries").upsert({
            "user_id": user_id,
            "summary": nouveau_resume,
        }).execute()

        # Purge les messages bruts maintenant condenses, pour ne pas
        # reconstruire indefiniment le meme resume a chaque appel suivant.
        ids_a_purger = [m["id"] for m in messages if m.get("id") is not None]
        if ids_a_purger:
            supabase.table("conversations").delete().in_("id", ids_a_purger).execute()

        logging.info(f"Résumé mémoire mis à jour pour user={user_id}.")
    except Exception as e:
        logging.error(f"ERREUR mise à jour résumé mémoire : {e}")


class _AttenteConfirmation(Exception):
    """
    Levee des qu'un outil sensible (ecriture) est rencontre, AVANT de
    l'executer. `appel` est l'appel en question ; `appels_restants` sont
    les appels du meme lot qui n'ont pas encore ete traites (ils seront
    rejoues a la reprise, dans l'ordre, apres que celui-ci ait ete
    confirme ou annule).
    """
    def __init__(self, appel, appels_restants):
        self.appel = appel
        self.appels_restants = appels_restants


def _executer_un_appel(appel, table_routage):
    try:
        arguments = json.loads(appel["arguments"] or "{}")
    except Exception:
        arguments = {}
    return appeler_outil(appel["name"], arguments, table_routage)


def _traiter_appels(appels, messages_agent, table_routage):
    """
    Execute une liste d'appels d'outils, en ajoutant le resultat de chacun
    a messages_agent au fur et a mesure. Des qu'un outil sensible
    (OUTILS_SENSIBLES) est rencontre, s'arrete AVANT de l'executer et leve
    _AttenteConfirmation avec les appels restants (lui inclus).

    Les appels "surs" qui precedent ce premier outil sensible (le cas le
    plus frequent : aucun outil sensible du tout dans le lot) sont
    executes EN PARALLELE plutot qu'un par un, pour ne pas payer en
    latence la somme des temps de reponse de chaque outil alors qu'ils
    sont independants les uns des autres (ex: deux recherches web
    simultanees). On ne parallelise jamais un outil sensible ni ce qui le
    suit : la garantie "on s'arrete avant de l'executer" doit rester
    valable meme dans le lot.
    """
    index_sensible = next(
        (i for i, appel in enumerate(appels) if appel["name"] in OUTILS_SENSIBLES),
        None,
    )
    appels_surs = appels if index_sensible is None else appels[:index_sensible]

    if appels_surs:
        for appel in appels_surs:
            yield {"type": "statut", "texte": f"{_nom_lisible(appel['name'])}..."}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(appels_surs)) as executor:
            futures = {
                executor.submit(_executer_un_appel, appel, table_routage): appel
                for appel in appels_surs
            }
            for future in concurrent.futures.as_completed(futures):
                appel = futures[future]
                resultat = future.result()
                yield {"type": "statut_termine", "texte": f"{_nom_lisible(appel['name'])} effectuée"}
                messages_agent.append({
                    "role": "tool",
                    "tool_call_id": appel["id"],
                    "content": resultat,
                })

    if index_sensible is not None:
        raise _AttenteConfirmation(appels[index_sensible], appels[index_sensible + 1:])


def _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele=GROQ_PRIMARY, reasoning_effort=None):
    appel = attente.appel
    try:
        arguments_dict = json.loads(appel["arguments"] or "{}")
    except Exception:
        arguments_dict = {}
    return {
        "type": "confirmation_requise",
        "nom_outil": appel["name"],
        "nom_lisible": _nom_lisible(appel["name"]),
        "arguments": arguments_dict,
        "etat_reprise": {
            "messages_agent": messages_agent,
            "outils_mcp": outils_mcp,
            "table_routage": table_routage,
            "appel": appel,
            "appels_restants": attente.appels_restants,
            "modele": modele,
            "reasoning_effort": reasoning_effort,
        },
    }


def _agent_groq(client_groq, messages_agent, outils_mcp, table_routage,
                 appels_en_cours_a_finir=None, modele=GROQ_PRIMARY, reasoning_effort=None):
    """
    Boucle d'agent generique sur le modele Groq utilise (par defaut
    GROQ_PRIMARY, mais peut recevoir n'importe quel modele Groq qui sait
    faire du tool calling -> permet de reutiliser cette meme boucle pour
    les modeles de secours de GROQ_FALLBACKS, avec les outils MCP branches
    dessus aussi, plutot que de les perdre des que GROQ_PRIMARY sature son
    quota TPM.

    `reasoning_effort`, si fourni (ex: "none"), est transmis tel quel a
    l'appel Groq : certains modeles de secours (ex: qwen3) font du
    raisonnement par defaut, ce qui peut etre desactive pour rester rapide.

    Genere des evenements "statut"/"reponse"/"confirmation_requise".
    S'arrete (sans exception) des qu'une reponse finale a ete produite OU
    qu'une confirmation est necessaire.

    `appels_en_cours_a_finir`, si fourni, est traite AVANT le prochain
    appel a Groq : c'est le cas lors d'une reprise apres confirmation, ou
    il faut d'abord finir le lot d'outils du tour precedent (executer les
    appels restants, ou re-demander confirmation si l'un d'eux est aussi
    sensible) avant de redemander une reponse au modele.
    """
    kwargs_reasoning = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}

    if appels_en_cours_a_finir:
        try:
            for event in _traiter_appels(appels_en_cours_a_finir, messages_agent, table_routage):
                yield event
        except _AttenteConfirmation as attente:
            yield _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele, reasoning_effort)
            return

    for _ in range(MAX_ETAPES_OUTILS):
        completion = client_groq.chat.completions.create(
            model=modele,
            messages=messages_agent,
            max_completion_tokens=1024,
            tools=outils_mcp if outils_mcp else None,
            stream=True,
            timeout=DELAI_MAX_PAR_APPEL,
            **kwargs_reasoning,
        )

        reponse_directe = False
        appels_en_cours = {}  # index -> {"id", "name", "arguments"}

        for chunk in completion:
            delta = chunk.choices[0].delta

            if delta.content:
                reponse_directe = True
                yield {"type": "reponse", "texte": delta.content}

            if delta.tool_calls:
                for fragment in delta.tool_calls:
                    etat = appels_en_cours.setdefault(
                        fragment.index, {"id": None, "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        etat["id"] = fragment.id
                    if fragment.function:
                        if fragment.function.name:
                            etat["name"] += fragment.function.name
                        if fragment.function.arguments:
                            etat["arguments"] += fragment.function.arguments

        if reponse_directe:
            logging.info(f"Réponse via GROQ (sans outil, streaming): {modele}")
            return

        if not appels_en_cours:
            return  # ni contenu ni outil (rare) : rien a faire de plus

        appels = [appels_en_cours[i] for i in sorted(appels_en_cours)]

        messages_agent.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": appel["id"],
                    "type": "function",
                    "function": {"name": appel["name"], "arguments": appel["arguments"]},
                }
                for appel in appels
            ],
        })

        try:
            for event in _traiter_appels(appels, messages_agent, table_routage):
                yield event
        except _AttenteConfirmation as attente:
            yield _evenement_confirmation(attente, messages_agent, outils_mcp, table_routage, modele, reasoning_effort)
            return

    # MAX_ETAPES_OUTILS epuise sans reponse directe : on force une reponse
    # finale (sans autoriser de nouvel appel d'outil).
    completion = client_groq.chat.completions.create(
        model=modele,
        messages=messages_agent,
        max_completion_tokens=1024,
        tools=outils_mcp if outils_mcp else None,
        stream=True,
        timeout=DELAI_MAX_PAR_APPEL,
        **kwargs_reasoning,
    )
    for chunk in completion:
        token = chunk.choices[0].delta.content or ""
        if token:
            yield {"type": "reponse", "texte": token}
    logging.info(f"Réponse via GROQ (avec outil): {modele}")


def _capturer_reponse(generateur, accumulateur):
    """
    Relaie tous les evenements d'un generateur tel quel, en accumulant au
    passage le texte des evenements "reponse" dans `accumulateur` (une
    liste, mutee en place). Permet de reconstruire la reponse finale
    complete une fois le generateur epuise, pour la persister en memoire,
    sans dupliquer cette logique a chaque point de sortie de chat().
    """
    for event in generateur:
        if event["type"] == "reponse":
            accumulateur.append(event["texte"])
        yield event


def chat(message_utilisateur=None, historique=None, user_id=None, reprise=None, agent_id=None, conversation_id=None, longueur_reponse="moyenne", image_url=None, localisation=None, fuseau_horaire=None, images_base64=None):
    """
    Generateur d'evenements. Chaque element produit est un dictionnaire :
    - {"type": "statut", "texte": "..."}         -> un outil MCP est en cours d'utilisation
    - {"type": "statut_termine", "texte": "..."} -> cet outil a fini (ou a ete annule)
    - {"type": "reponse", "texte": "..."}        -> morceau de la reponse finale (streaming)
    - {"type": "confirmation_requise", ...}      -> un outil qui MODIFIE les donnees de
      l'etudiant (ex: creer une page Notion) attend une confirmation avant de s'executer.
      Contient "nom_lisible", "arguments" (a afficher a l'etudiant), et "etat_reprise"
      (a repasser tel quel a chat(reprise=...) une fois la decision prise).
    - {"type": "meta", "message_id_user": ..., "message_id_assistant": ...,
      "created_at_assistant": ...}                -> DERNIER evenement emis, une fois
      l'echange persiste dans historique_conversations (voir _sauvegarder_echange).
      Ids necessaires cote appelant (API de migration Next.js) pour indexer un
      feedback like/dislike sur CE message precis (voir
      MIGRATION_CHAT_VERS_NEXTJS.md, section 3.2). Absent si l'etudiant n'est pas
      connecte (user_id=None) : dans ce cas aucun feedback n'est possible non plus.

    faces/app_etudiant.py doit distinguer ces types pour savoir quoi afficher, et ne
    garder que "reponse" dans l'historique de conversation.

    `longueur_reponse` (optionnel, "courte" | "moyenne" | "longue", defaut
    "moyenne" = comportement historique inchange) pilote la longueur de la
    reponse generee via une consigne ajoutee au prompt systeme (voir
    INSTRUCTIONS_LONGUEUR_REPONSE). Migration Next.js, section 3.3 :
    modifiable a chaque message par l'etudiant.

    `user_id` (session.user.id de Supabase Auth, ou None si l'etudiant n'est
    pas connecte) est transmis au registre d'outils pour que les outils "par
    utilisateur" (ex: Notion) sachent pour qui aller chercher un token. Il sert
    aussi a scoper la memoire long-terme (conversation_summaries, scope par
    user_id seul depuis le compte unifie de juillet 2026 -> le resume suit
    l'etudiant d'un agent a l'autre, pas cloisonne par agent) : sans user_id
    (etudiant non connecte), rien n'est lu ni ecrit en memoire.

    `agent_id` (optionnel) determine quel prompt systeme et quelles donnees
    RAG utiliser (voir configuration.py / retriever.py). Si non fourni, on
    utilise le secret AGENT_ID du deploiement, puis AGENT_ID_PAR_DEFAUT.

    `conversation_id` (optionnel, 2026-07-13) identifie le fil de
    discussion affiche dans la sidebar de chat.py (liste de conversations
    distinctes et cliquables, façon Claude.ai) -- genere cote chat.py, une
    valeur par conversation, pas par message. Simplement transmis a
    _sauvegarder_echange(). None accepte : un appelant qui ne gere pas
    encore les fils continue de fonctionner normalement.

    Pour reprendre apres une confirmation_requise, appeler :
        chat(reprise={"etat_reprise": evenement["etat_reprise"], "approuve": True|False})
    (message_utilisateur/historique/user_id sont alors ignores.)
    LIMITE CONNUE : la memoire long-terme n'est PAS persistee sur ce chemin de
    reprise (etat_reprise ne transporte ni agent_id, ni user_id, ni le message
    utilisateur d'origine, ni conversation_id). A etendre si besoin en les
    ajoutant a etat_reprise dans _evenement_confirmation.

    `image_url` (optionnel, 2026-07-20) : URL publique d'une image jointe au
    message (voir api/uploads.py:uploader_image_chat). Si presente, on ne
    passe PAS par le cascade Groq habituel (aucun des modeles Groq de
    GROQ_PRIMARY/GROQ_FALLBACKS n'est multimodal) : on route directement et
    uniquement vers Gemini, seul modele vision de la cascade. Consequence
    connue : pas d'outils MCP (Notion, Wolfram, recherche web) sur un
    message avec image, comme pour le fallback Gemini texte plus bas. Si
    Gemini echoue sur ce chemin, on renvoie MESSAGE_ERREUR direct (pas de
    retry cascade complet comme pour le texte : un seul modele disponible).

    `localisation` (optionnel, 2026-07-20) : dict {"latitude":..., "longitude":...}
    transmis explicitement par l'etudiant (bouton dedie, jamais automatique).
    Injecte en fin de prompt systeme, jamais traite comme un fait dit par
    l'etudiant. N'affecte ni le cascade ni le choix de modele.

    `fuseau_horaire` (optionnel, 2026-07-20) : nom de fuseau IANA lu depuis
    le navigateur (Intl.DateTimeFormat().resolvedOptions().timeZone, voir
    ChatIA.tsx:envoyerMessage). PAS de fuseau fixe côté serveur -- Djiguignè
    est panafricain, aucune hypothèse de pays. Repli sur UTC si absent ou
    invalide.

    `images_base64` (optionnel, 2026-07-20) : liste de frames JPEG en
    base64, extraites d'une vidéo uploadée (voir
    api/uploads.py:uploader_video_chat et core/video.py:_extraire_frames_video).
    Combinable avec image_url (rare en pratique) -- toutes les images sont
    envoyées à Gemini dans le MÊME message. Le son de la vidéo n'est PAS
    envoyé ici : il est transcrit à part (Whisper) et injecté comme texte
    dans message_utilisateur par le frontend, avant l'appel à chat().

    Liens colles dans message_utilisateur (page web ou video YouTube) :
    recuperes automatiquement (_enrichir_message_avec_urls) et ajoutes en
    contexte APRES le message original avant envoi au modele. Le message
    BRUT (sans ce contenu) reste ce qui est sauvegarde dans l'historique.

    Si TOUS les maillons de la cascade (Groq principal, Gemini, fallbacks
    Groq) echouent uniquement a cause d'un timeout, on retente une seconde
    fois toute la cascade. Si au moins une erreur n'est pas un timeout (ex:
    429, cle invalide...), on ne retente pas et on part direct sur le
    message d'erreur.
    """
    if reprise is not None:
        etat = reprise["etat_reprise"]
        approuve = reprise["approuve"]
        messages_agent = etat["messages_agent"]
        outils_mcp = etat["outils_mcp"]
        table_routage = etat["table_routage"]
        appel = etat["appel"]
        modele_reprise = etat.get("modele", GROQ_PRIMARY)
        reasoning_effort_reprise = etat.get("reasoning_effort")

        client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)

        if approuve:
            yield {"type": "statut", "texte": f"{_nom_lisible(appel['name'])}..."}
            try:
                arguments = json.loads(appel["arguments"] or "{}")
            except Exception:
                arguments = {}
            resultat = appeler_outil(appel["name"], arguments, table_routage)
            yield {"type": "statut_termine", "texte": f"{_nom_lisible(appel['name'])} effectuée"}
        else:
            resultat = "Action annulée par l'étudiant : cet outil n'a pas été exécuté."
            yield {"type": "statut_termine", "texte": f"{_nom_lisible(appel['name'])} annulée"}

        messages_agent.append({
            "role": "tool",
            "tool_call_id": appel["id"],
            "content": resultat,
        })

        try:
            yield from _agent_groq(
                client_groq, messages_agent, outils_mcp, table_routage,
                appels_en_cours_a_finir=etat.get("appels_restants") or None,
                modele=modele_reprise, reasoning_effort=reasoning_effort_reprise,
            )
        except Exception as e:
            logging.error(f"ERREUR GROQ (reprise apres confirmation) {modele_reprise}: {e}")
            yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    # --- Chemin normal : nouvelle question --------------------------------
    if historique is None:
        historique = []

    if agent_id is None:
        agent_id = get_secret("AGENT_ID") or AGENT_ID_PAR_DEFAUT

    system_final = _construire_system_prompt(message_utilisateur, agent_id, user_id, longueur_reponse, fuseau_horaire)

    if localisation and localisation.get("latitude") is not None and localisation.get("longitude") is not None:
        # Contexte "système/environnement" (2026-07-20) : position GPS
        # transmise explicitement par l'étudiant (bouton dédié côté
        # frontend, jamais automatique/silencieux -- voir BarreDeSaisie.tsx
        # et la permission navigateur navigator.geolocation). Ajoutée en
        # fin de prompt système, jamais comme un fait affirmé par
        # l'étudiant lui-même.
        system_final += (
            "\n\nContexte de localisation (fourni par le navigateur de "
            "l'étudiant, à utiliser seulement si pertinent pour la "
            f"question) : latitude {localisation['latitude']}, "
            f"longitude {localisation['longitude']}."
        )

    # Liens collés dans le message (page web ou vidéo YouTube) : récupérés
    # ICI, sur le message pour le modèle uniquement -- message_utilisateur
    # (brut, sans le contenu des liens) reste ce qui est sauvegardé dans
    # l'historique via _sauvegarder_echange plus bas.
    message_pour_modele = _enrichir_message_avec_urls(message_utilisateur)

    messages_base = [{"role": "system", "content": system_final}]
    messages_base += historique
    messages_base.append({"role": "user", "content": message_pour_modele})

    if image_url or images_base64:
        # Chemin dédié image(s) : voir docstring ci-dessus. Pas de cascade
        # multi-modeles ici, Gemini est le seul maillon capable de traiter
        # de la vision -- s'il echoue, il n'y a pas de second recours
        # multimodal. `images_base64` (2026-07-20) : frames extraites d'une
        # vidéo par _extraire_frames_video, voir la branche vidéo dédiée
        # dans api/uploads.py:uploader_video_chat -- même mécanique que
        # l'image simple, juste plusieurs inline_data au lieu d'un seul.
        images = []
        if image_url:
            try:
                images.append(_telecharger_image(image_url))
            except Exception as e:
                logging.error(f"ERREUR TELECHARGEMENT IMAGE ({image_url}): {e}")
                yield {"type": "reponse", "texte": "Désolé, je n'ai pas pu récupérer l'image envoyée. Réessaie."}
                return
        if images_base64:
            for image_b64 in images_base64:
                images.append((base64.b64decode(image_b64), "image/jpeg"))

        gemini_messages = [
            {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
            for m in messages_base[:-1] if m["role"] != "system"
        ]
        gemini_messages.append({
            "role": "user",
            "parts": _construire_parts_gemini(message_pour_modele, images),
        })

        reponse_accumulee = []
        try:
            client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
            response = client_google.models.generate_content_stream(
                model=GOOGLE_MODEL,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_final,
                    max_output_tokens=1024
                )
            )
            for chunk in response:
                if chunk.text:
                    reponse_accumulee.append(chunk.text)
                    yield {"type": "reponse", "texte": chunk.text}
            logging.info("Réponse via GEMINI (image)")
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
        except Exception as e:
            logging.error(f"ERREUR GEMINI (image): {e}")
            if not reponse_accumulee:
                yield {"type": "reponse", "texte": MESSAGE_ERREUR}
        return

    client_groq = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0)
    outils_mcp, table_routage = lister_tous_les_outils(get_secret, user_id, agent_id)

    for _passage in range(MAX_PASSAGES_CASCADE):
        tout_est_timeout = True

        # Une SEULE liste de messages pour tout ce passage de la cascade
        # Groq (modele principal + fallbacks), au lieu d'en recreer une a
        # chaque modele. Raison : si un modele a deja appele un outil (ex:
        # notion-search) et obtenu un resultat AVANT d'echouer sur l'appel
        # Groq suivant (429/413 en essayant de rediger la reponse finale),
        # le resultat de cet outil est deja present dans messages_agent
        # (ajoute par _agent_groq/_traiter_appels). Si on repartait de
        # messages_base a chaque modele, ce resultat serait perdu et le
        # modele de secours suivant redemarrerait a zero, sans le contexte
        # deja recupere (cause du bug ou la page Notion trouvee n'arrivait
        # jamais dans la reponse finale).
        messages_agent = list(messages_base)
        reponse_accumulee = []

        # 1. GPT-OSS 120B, avec cycle d'outils MCP dynamique
        try:
            yield from _capturer_reponse(
                _agent_groq(client_groq, messages_agent, outils_mcp, table_routage),
                reponse_accumulee,
            )
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            return
        except Exception as e:
            if not _est_timeout(e):
                tout_est_timeout = False
                logging.error(f"ERREUR GROQ {GROQ_PRIMARY}: {e}")

        # 2. Fallbacks Groq — AVEC les memes outils MCP (via _agent_groq),
        # pour que Notion/Wolfram restent utilisables meme quand
        # GROQ_PRIMARY sature son quota TPM (ce qui est le cas le plus
        # frequent de bascule ici, pas une vraie panne du modele).
        # reasoning_effort="none" : ces modeles (ex: qwen3) font du
        # raisonnement par defaut, on le desactive pour rester rapide,
        # comme avant cette modification.
        # IMPORTANT : on reutilise messages_agent tel quel (meme instance,
        # mutee en place par _agent_groq) d'un modele a l'autre — on ne le
        # reinitialise PAS a messages_base a chaque tour de boucle (voir
        # commentaire ci-dessus).
        for model in GROQ_FALLBACKS:
            try:
                reasoning_pour_ce_modele = "none" if model in MODELES_AVEC_REASONING_EFFORT else None
                yield from _capturer_reponse(
                    _agent_groq(
                        client_groq, messages_agent, outils_mcp, table_routage,
                        modele=model, reasoning_effort=reasoning_pour_ce_modele,
                    ),
                    reponse_accumulee,
                )
                ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id)
                if ids_historique:
                    yield {"type": "meta", **ids_historique}
                _mettre_a_jour_resume_si_besoin(user_id)
                return
            except Exception as e:
                if not _est_timeout(e):
                    tout_est_timeout = False
                    logging.error(f"ERREUR GROQ {model}: {e}")
                continue

        # 3. Gemini 2.5 Flash — tout dernier recours, sans outils MCP.
        # Utile seulement si TOUS les modeles Groq (principal + fallbacks)
        # sont indisponibles en meme temps ; dans ce cas l'etudiant a au
        # moins une reponse texte, mais sans acces a Notion/Wolfram.
        try:
            client_google = genai.Client(api_key=get_secret("GOOGLE_API_KEY"))
            gemini_messages = [
                {"role": "user" if m["role"] != "assistant" else "model", "parts": [{"text": m["content"]}]}
                for m in messages_base if m["role"] != "system"
            ]
            response = client_google.models.generate_content_stream(
                model=GOOGLE_MODEL,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_final,
                    max_output_tokens=1024
                )
            )
            for chunk in response:
                if chunk.text:
                    reponse_accumulee.append(chunk.text)
                    yield {"type": "reponse", "texte": chunk.text}
            logging.info("Réponse via GEMINI")
            ids_historique = _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee), conversation_id)
            if ids_historique:
                yield {"type": "meta", **ids_historique}
            _mettre_a_jour_resume_si_besoin(user_id)
            return
        except Exception as e:
            if not _est_timeout(e):
                tout_est_timeout = False
            logging.error(f"ERREUR GEMINI: {e}")

        if not tout_est_timeout:
            break  # au moins une vraie erreur (pas juste lent) : inutile de retenter

        logging.info("Toute la cascade a timeout, on retente un passage complet.")

    # Echec complet : on ne persiste jamais un message d'erreur technique
    # en memoire (polluerait le resume avec du bruit sans valeur).
    yield {"type": "reponse", "texte": MESSAGE_ERREUR}

