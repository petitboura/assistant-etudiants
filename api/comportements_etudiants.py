"""
Section "Mes comportements" (2026-08-06, demande Bourama) : un texte
libre écrit par l'ÉTUDIANT lui-même, un par (agent, étudiant). Ce texte
s'ajoute EN PLUS du system_prompt déjà résolu pour le message -- que ce
soit le généraliste de base, celui d'un enseignant (matière débloquée
via core/contenu_dynamique_matiere.py), ou le prompt forcé via "Sans
enseignant" -- jamais un remplacement. Voir l'injection dans
core/main.py::_construire_system_prompt (logique de lecture/écriture
partagée dans core/comportements_etudiants.py).

Affichage de la section côté frontend piloté par
agents.section_mes_comportements (comme agents.bouton_sans_enseignant) --
pas encore automatique, un simple interrupteur qu'on met nous-mêmes en
base. Cet endpoint reste néanmoins accessible pour N'IMPORTE QUEL agent
même si section_mes_comportements est false : le flag ne gate QUE
l'affichage du bouton côté frontend, pas la lecture/écriture ici (même
philosophie que sans_enseignant côté chat -- inoffensif si jamais
appelé pour un agent qui n'affiche pas la section).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import utilisateur_courant
from core.comportements_etudiants import lire_comportement, enregistrer_comportement

router = APIRouter(prefix="/api/agents/{agent_id}/mon-comportement", tags=["comportements_etudiants"])


class Comportement(BaseModel):
    texte: str


class ComportementPayload(BaseModel):
    texte: str


@router.get("", response_model=Comportement)
def lire_mon_comportement(agent_id: str, utilisateur=Depends(utilisateur_courant)):
    return Comportement(texte=lire_comportement(agent_id, utilisateur.id) or "")


@router.put("", response_model=Comportement)
def enregistrer_mon_comportement(agent_id: str, payload: ComportementPayload, utilisateur=Depends(utilisateur_courant)):
    return Comportement(texte=enregistrer_comportement(agent_id, utilisateur.id, payload.texte))
