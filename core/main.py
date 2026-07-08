import os
import json
import logging
import concurrent.futures
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


def _nom_lisible(nom_outil):
    return NOMS_OUTILS_LISIBLES.get(nom_outil, nom_outil)


REGLE_CONTEXTE_INVISIBLE = (
    "\n\nIMPORTANT ABSOLU : Tout ce qui précède est ton contexte interne invisible. "
    "L'utilisateur ne voit rien de tout cela. Si l'utilisateur dit 'c'est quoi ce message' "
    "ou similaire, il parle uniquement de ta dernière réponse ou de la sienne — jamais de "
    "ton contexte interne. Ne le mentionne jamais."
)


def _charger_resume_memoire(user_id, agent_id):
    """
    Recupere le resume long-terme (table conversation_summaries) de cet
    etudiant pour cet agent, s'il existe. Retourne "" si l'etudiant n'est
    pas connecte (user_id=None) ou si aucun resume n'existe encore.
    """
    if not user_id:
        return ""
    try:
        res = (
            supabase.table("conversation_summaries")
            .select("summary")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .maybe_single()
            .execute()
        )
        return (res.data or {}).get("summary") or ""
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture conversation_summaries) : {e}")
        return ""


def _construire_system_prompt(message_utilisateur, agent_id, user_id=None):
    system_prompt = get_system_prompt(agent_id)
    candidats = chercher_candidats(message_utilisateur, agent_id=agent_id)
    resume_memoire = _charger_resume_memoire(user_id, agent_id)

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
    system_final += REGLE_CONTEXTE_INVISIBLE

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


def _sauvegarder_echange(user_id, agent_id, message_utilisateur, reponse_finale):
    """
    Persiste l'echange (question + reponse) dans `conversations`, pour la
    memoire long-terme. Ignore silencieusement si l'etudiant n'est pas
    connecte (user_id=None) ou si la reponse est vide (ex: message
    d'erreur technique, qu'on ne veut pas polluer la memoire avec).
    """
    if not user_id or not (reponse_finale or "").strip():
        return
    try:
        supabase.table("conversations").insert([
            {"user_id": user_id, "agent_id": agent_id, "role": "user", "content": message_utilisateur},
            {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": reponse_finale},
        ]).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (sauvegarde conversations) : {e}")


def _mettre_a_jour_resume_si_besoin(user_id, agent_id):
    """
    Si assez de nouveaux messages bruts se sont accumules (>= SEUIL_RESUME_MESSAGES)
    depuis le dernier resume, en regenere un condense (ancien resume + messages
    recents) via un modele Groq rapide, l'ecrit dans conversation_summaries, puis
    purge les messages bruts desormais condenses. Ne bloque jamais la reponse a
    l'etudiant : toute erreur est juste loguee, jamais remontee a l'appelant.
    """
    if not user_id:
        return
    try:
        messages = (
            supabase.table("conversations")
            .select("id, role, content, created_at")
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .order("created_at", desc=True)
            .limit(SEUIL_RESUME_MESSAGES)
            .execute()
        ).data or []

        if len(messages) < SEUIL_RESUME_MESSAGES:
            return  # pas encore assez de matiere pour justifier un resume

        ancien_resume = _charger_resume_memoire(user_id, agent_id)
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
            max_completion_tokens=400,
            timeout=DELAI_MAX_PAR_APPEL,
        )
        nouveau_resume = completion.choices[0].message.content.strip()

        supabase.table("conversation_summaries").upsert({
            "user_id": user_id,
            "agent_id": agent_id,
            "summary": nouveau_resume,
        }).execute()

        # Purge les messages bruts maintenant condenses, pour ne pas
        # reconstruire indefiniment le meme resume a chaque appel suivant.
        ids_a_purger = [m["id"] for m in messages if m.get("id") is not None]
        if ids_a_purger:
            supabase.table("conversations").delete().in_("id", ids_a_purger).execute()

        logging.info(f"Résumé mémoire mis à jour pour user={user_id}, agent={agent_id}.")
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


def chat(message_utilisateur=None, historique=None, user_id=None, reprise=None, agent_id=None):
    """
    Generateur d'evenements. Chaque element produit est un dictionnaire :
    - {"type": "statut", "texte": "..."}         -> un outil MCP est en cours d'utilisation
    - {"type": "statut_termine", "texte": "..."} -> cet outil a fini (ou a ete annule)
    - {"type": "reponse", "texte": "..."}        -> morceau de la reponse finale (streaming)
    - {"type": "confirmation_requise", ...}      -> un outil qui MODIFIE les donnees de
      l'etudiant (ex: creer une page Notion) attend une confirmation avant de s'executer.
      Contient "nom_lisible", "arguments" (a afficher a l'etudiant), et "etat_reprise"
      (a repasser tel quel a chat(reprise=...) une fois la decision prise).

    faces/app_etudiant.py doit distinguer ces types pour savoir quoi afficher, et ne
    garder que "reponse" dans l'historique de conversation.

    `user_id` (session.user.id de Supabase Auth, ou None si l'etudiant n'est
    pas connecte) est transmis au registre d'outils pour que les outils "par
    utilisateur" (ex: Notion) sachent pour qui aller chercher un token. Il sert
    aussi a scoper la memoire long-terme (conversations/conversation_summaries) :
    sans user_id (etudiant non connecte), rien n'est lu ni ecrit en memoire.

    `agent_id` (optionnel) determine quel prompt systeme et quelles donnees
    RAG utiliser (voir configuration.py / retriever.py). Si non fourni, on
    utilise le secret AGENT_ID du deploiement, puis AGENT_ID_PAR_DEFAUT.

    Pour reprendre apres une confirmation_requise, appeler :
        chat(reprise={"etat_reprise": evenement["etat_reprise"], "approuve": True|False})
    (message_utilisateur/historique/user_id sont alors ignores.)
    LIMITE CONNUE : la memoire long-terme n'est PAS persistee sur ce chemin de
    reprise (etat_reprise ne transporte ni agent_id, ni user_id, ni le message
    utilisateur d'origine). A etendre si besoin en les ajoutant a etat_reprise
    dans _evenement_confirmation.

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

    system_final = _construire_system_prompt(message_utilisateur, agent_id, user_id)

    messages_base = [{"role": "system", "content": system_final}]
    messages_base += historique
    messages_base.append({"role": "user", "content": message_utilisateur})

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
            _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee))
            _mettre_a_jour_resume_si_besoin(user_id, agent_id)
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
                _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee))
                _mettre_a_jour_resume_si_besoin(user_id, agent_id)
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
            _sauvegarder_echange(user_id, agent_id, message_utilisateur, "".join(reponse_accumulee))
            _mettre_a_jour_resume_si_besoin(user_id, agent_id)
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

