"""
Proactivité (25/07) : un agent peut relancer un utilisateur inactif de sa
propre initiative, sans que celui-ci n'ait rien demandé -- l'inverse du
planificateur de rappels (core/notifications_push.py), qui n'agit que sur
demande explicite ("préviens-moi dans 3 jours de...").

Générique par construction (demande explicite de Bourama, 25/07) : ce
module ne connaît RIEN du domaine d'un agent en particulier (tutorat,
coaching business, écriture créative...) -- c'est l'agent lui-même, via
son propre prompt système + la conversation passée, qui juge de la
pertinence d'une relance (voir _decider_relance). Ce fichier ne fait que
détecter l'inactivité et déclencher la décision, jamais le contenu.

Double opt-in obligatoire (voir migration proactivite_relances) :
- agents.proactivite_active : le CRÉATEUR autorise cet agent à relancer.
- profiles.notifications_proactives_actives : l'UTILISATEUR autorise SES
  agents à le relancer.
Sans les deux, aucune relance n'est jamais envoyée à personne.

Boucle appelante : voir api/main.py (_boucle_planificateur_proactivite).
Tourne beaucoup moins souvent que celle des rappels -- l'inactivité se
mesure en jours, pas en minutes.
"""

import logging
from datetime import datetime, timezone, timedelta

from groq import Groq

from api.auth import supabase
from core.main import get_secret, GROQ_PRIMARY, _construire_system_prompt, _ressemble_a_du_json_casse
from core.notifications_push import envoyer_notification_push, notifications_push_disponible

SEUIL_INACTIVITE = timedelta(days=4)  # pas de nouvelle depuis X jours -> candidat à une relance
COOLDOWN_VERIFICATION = timedelta(hours=6)  # ne re-vérifie pas une paire (agent, utilisateur) plus souvent que ça
COOLDOWN_RELANCE = timedelta(days=7)  # ne relance pas la même paire plus d'une fois par semaine
NB_MESSAGES_CONTEXTE = 20  # historique récent donné au modèle pour juger

SENTINELLE_AUCUNE_RELANCE = "AUCUNE_RELANCE"

INSTRUCTION_PROACTIVITE = f"""

DÉCISION DE RELANCE PROACTIVE : la personne ci-dessus ne t'a pas écrit
depuis plusieurs jours (voir la conversation). C'est TOI qui prends
l'initiative de la contacter, elle n'a rien demandé.

Décide si une relance est vraiment pertinente et utile pour CETTE
personne précise, sur la base de ce que vous vous êtes dit. Ne relance QUE
s'il y a une vraie raison concrète ancrée dans la conversation (un
objectif qu'elle a mentionné, une échéance, quelque chose resté
inachevé) -- jamais une relance générique du style "tu es là ?" ou "des
nouvelles ?" sans contenu réel.

- Si une relance est pertinente : réponds UNIQUEMENT avec le message à
  lui envoyer directement (court, naturel, dans ton style habituel --
  PAS "Je me permets de vous relancer", plutôt comme si tu reprenais le
  fil normalement).
- Si aucune relance n'est pertinente : réponds EXACTEMENT et UNIQUEMENT
  "{SENTINELLE_AUCUNE_RELANCE}", rien d'autre, aucune ponctuation en plus.
"""


def _marquer_verification(user_id: str, agent_id: str) -> None:
    """
    UPDATE puis INSERT si absent (PAS un upsert naïf) : un upsert sur les
    seules colonnes fournies écraserait derniere_relance_envoyee_a à NULL
    si la ligne existe déjà, puisque cette colonne ne serait pas incluse
    dans le payload.
    """
    maintenant = datetime.now(timezone.utc).isoformat()
    try:
        res = (
            supabase.table("relances_proactives")
            .update({"derniere_verification_a": maintenant})
            .eq("user_id", user_id)
            .eq("agent_id", agent_id)
            .execute()
        )
        if not res.data:
            supabase.table("relances_proactives").insert(
                {"user_id": user_id, "agent_id": agent_id, "derniere_verification_a": maintenant}
            ).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer vérification relance, user={user_id}, agent={agent_id}) : {e}")


def _marquer_relance_envoyee(user_id: str, agent_id: str) -> None:
    try:
        supabase.table("relances_proactives").update(
            {"derniere_relance_envoyee_a": datetime.now(timezone.utc).isoformat()}
        ).eq("user_id", user_id).eq("agent_id", agent_id).execute()
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (marquer relance envoyée, user={user_id}, agent={agent_id}) : {e}")


def _decider_relance(agent_id: str, user_id: str) -> str | None:
    """
    Laisse l'agent (son propre prompt système + la conversation passée)
    juger de la pertinence d'une relance. Renvoie le message à envoyer,
    ou None si aucune relance n'est pertinente (ou en cas d'erreur --
    fail-silent, une relance ratée n'est jamais grave, contrairement à un
    message bloqué à tort dans le chat normal).
    """
    try:
        historique = (
            supabase.table("historique_conversations")
            .select("role, content, created_at")
            .eq("agent_id", agent_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(NB_MESSAGES_CONTEXTE)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture historique décision relance, user={user_id}, agent={agent_id}) : {e}")
        return None

    messages_recents = list(reversed(historique.data or []))
    if not messages_recents:
        return None

    try:
        # message_utilisateur="" : pas de nouveau message pour le RAG ici,
        # on garde quand même persona + mémoire + profil (voir
        # _construire_system_prompt dans core/main.py, réutilisée telle
        # quelle pour rester cohérent avec le vrai chat).
        system_final = _construire_system_prompt("", agent_id, user_id, longueur_reponse="courte")
    except Exception as e:
        logging.error(f"ERREUR construction prompt (décision relance, agent={agent_id}) : {e}")
        return None
    system_final += INSTRUCTION_PROACTIVITE

    messages = [{"role": "system", "content": system_final}]
    for m in messages_recents:
        role = "assistant" if m["role"] == "assistant" else "user"
        messages.append({"role": role, "content": m["content"]})
    messages.append(
        {"role": "user", "content": "[Aucun nouveau message -- décide si tu relances, selon les consignes ci-dessus.]"}
    )

    try:
        client = Groq(api_key=get_secret("GROQ_API_KEY"), max_retries=0, timeout=20.0)
        completion = client.chat.completions.create(model=GROQ_PRIMARY, messages=messages)
        texte = (completion.choices[0].message.content or "").strip()
    except Exception as e:
        logging.error(f"ERREUR Groq (décision relance, agent={agent_id}, user={user_id}) : {e}")
        return None

    if not texte or texte.strip().upper() == SENTINELLE_AUCUNE_RELANCE:
        return None
    if _ressemble_a_du_json_casse(texte):
        logging.warning(f"Relance ignorée (réponse suspecte, agent={agent_id}, user={user_id}).")
        return None
    return texte


def verifier_relances_proactives() -> int:
    """
    Appelée périodiquement (voir api/main.py). Pour chaque agent avec
    proactivite_active=true, cherche les utilisateurs inactifs depuis
    SEUIL_INACTIVITE, ayant activé notifications_proactives_actives, pas
    vérifiés depuis COOLDOWN_VERIFICATION ni relancés depuis
    COOLDOWN_RELANCE -- puis laisse l'agent décider (_decider_relance).
    Renvoie le nombre de relances effectivement envoyées.
    """
    if not notifications_push_disponible():
        return 0

    try:
        agents_actifs = (
            supabase.table("agents")
            .select("id, nom")
            .eq("proactivite_active", True)
            .eq("actif", True)
            .execute()
        )
    except Exception as e:
        logging.error(f"ERREUR SUPABASE (lecture agents proactifs) : {e}")
        return 0

    maintenant = datetime.now(timezone.utc)
    seuil_inactivite = (maintenant - SEUIL_INACTIVITE).isoformat()
    envoyees = 0

    for agent in agents_actifs.data or []:
        agent_id = agent["id"]

        # Dernier message (question OU réponse) par utilisateur pour cet
        # agent. NOTE : lit jusqu'à 500 lignes récentes puis déduplique
        # côté Python -- suffisant au volume actuel (voir commentaire de
        # migration), à revoir avec une vraie colonne "dernière activité"
        # si le nombre de messages par agent grossit beaucoup.
        try:
            derniers_messages = (
                supabase.table("historique_conversations")
                .select("user_id, created_at")
                .eq("agent_id", agent_id)
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
        except Exception as e:
            logging.error(f"ERREUR SUPABASE (lecture historique agent={agent_id}) : {e}")
            continue

        dernier_message_par_utilisateur = {}
        for ligne in derniers_messages.data or []:
            uid = ligne["user_id"]
            if uid not in dernier_message_par_utilisateur:
                dernier_message_par_utilisateur[uid] = ligne["created_at"]

        for user_id, dernier_message_a in dernier_message_par_utilisateur.items():
            if dernier_message_a > seuil_inactivite:
                continue  # encore actif, rien à faire

            try:
                profil = (
                    supabase.table("profiles")
                    .select("notifications_proactives_actives")
                    .eq("user_id", user_id)
                    .maybe_single()
                    .execute()
                )
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (lecture profil user={user_id}) : {e}")
                continue
            if not profil.data or not profil.data.get("notifications_proactives_actives"):
                continue  # opt-out côté utilisateur -- on n'insiste jamais

            try:
                suivi = (
                    supabase.table("relances_proactives")
                    .select("derniere_verification_a, derniere_relance_envoyee_a")
                    .eq("user_id", user_id)
                    .eq("agent_id", agent_id)
                    .maybe_single()
                    .execute()
                )
            except Exception as e:
                logging.error(f"ERREUR SUPABASE (lecture suivi relance user={user_id}, agent={agent_id}) : {e}")
                suivi = None

            if suivi and suivi.data:
                derniere_verif = suivi.data.get("derniere_verification_a")
                if derniere_verif and derniere_verif > (maintenant - COOLDOWN_VERIFICATION).isoformat():
                    continue  # déjà vérifié récemment, pas la peine de re-décider
                derniere_relance = suivi.data.get("derniere_relance_envoyee_a")
                if derniere_relance and derniere_relance > (maintenant - COOLDOWN_RELANCE).isoformat():
                    _marquer_verification(user_id, agent_id)
                    continue  # relancé récemment, on laisse respirer

            message_relance = _decider_relance(agent_id, user_id)
            _marquer_verification(user_id, agent_id)

            if not message_relance:
                continue

            try:
                envoyer_notification_push(user_id, agent.get("nom") or "Nouveau message", message_relance)
                supabase.table("conversations").insert(
                    {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": message_relance}
                ).execute()
                supabase.table("historique_conversations").insert(
                    {"user_id": user_id, "agent_id": agent_id, "role": "assistant", "content": message_relance}
                ).execute()
                _marquer_relance_envoyee(user_id, agent_id)
                envoyees += 1
            except Exception as e:
                logging.error(f"ERREUR envoi relance proactive (user={user_id}, agent={agent_id}) : {e}")

    return envoyees
