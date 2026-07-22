# Génération de documents / code / images -- étapes manuelles

Ajouté le 2026-07-20. Le code est prêt, mais 3 choses ne peuvent pas être
faites depuis un dépôt Git, à faire toi-même :

## 1. Créer le bucket Supabase (obligatoire, sinon RIEN ne fonctionne)

Dans le dashboard Supabase -> Storage -> New bucket :
- Nom exact : `generations`
- Public : **oui** (comme `images-publiques`, même logique que
  `api/uploads.py` -- pas de policy RLS, tout passe par le service role
  côté Python)

## 2. Activer l'outil pour un agent (obligatoire, sinon l'agent ne voit jamais l'outil)

Dans la table `agents` de Supabase, colonne `tools_enabled` : ajoute
`"generation"` à la liste pour chaque agent qui doit pouvoir générer des
documents/code/images (ex. l'agent Djiguignè principal). Sans ça,
`_outils_actives_pour_agent()` (core/mcp_tools.py) filtre l'outil, même
si le code est bien déployé.

## 3. Dépendances système pour WeasyPrint (à vérifier sur Railway)

WeasyPrint (génération de PDF) a besoin de librairies systeme (Pango,
cairo, GDK-Pixbuf), pas seulement du package Python. Sur Railway
(Nixpacks), il faut probablement un fichier `nixpacks.toml` ou
équivalent listant ces paquets, sinon le déploiement plantera au premier
`pip install weasyprint` ou au premier appel. Teste en local d'abord
(`pip install weasyprint --break-system-packages` puis un essai simple)
avant de déployer, pour isoler l'erreur si ça casse.

## 4. Génération d'images (ACTIF DÈS MAINTENANT, gratuit par défaut)

Depuis le 21/07/2026 : Pollinations.ai (gratuit, sans clé) est utilisé
par défaut, donc `generer_image` fonctionne déjà, sans rien à
configurer. Si tu ajoutes `TOGETHER_API_KEY` plus tard (~0,003$/image),
le code bascule automatiquement vers Together AI (meilleure qualité/
fiabilité), sans rien à changer dans le code.

**Non testé en conditions réelles** (Pollinations n'était pas
accessible depuis l'environnement de développement, restriction de
bac à sable) : à vérifier au premier vrai test, comme d'habitude.

## 5. Signature électronique (gratuit jusqu'à 5/mois, aucune urgence budget)

Contrairement à Together AI, Lumin (developers.luminpdf.com) est
gratuit jusqu'à 5 signatures/mois, donc rien n'empêche de l'activer dès
maintenant :
1. Connecte-toi sur Lumin -> Settings -> Developer settings -> API keys
   -> Generate key
2. Ajoute `LUMIN_API_KEY` dans les variables d'environnement Railway
Même détection automatique que pour Together AI (`signature_disponible()`
dans core/generation_signature.py).

## 6. Audio / synthèse vocale (Kokoro gratuit par défaut, Groq payant en option)

Depuis le 21/07/2026, deux chemins possibles :

**Gratuit (par défaut si configuré)** : Kokoro-82M via Hugging Face.
1. Crée un compte gratuit sur huggingface.co (aucune carte bancaire)
2. Génère un token : Settings -> Access Tokens -> New token
3. Ajoute `HF_API_TOKEN` dans les variables d'environnement Railway

**Payant (optionnel, meilleure latence)** : Groq/Orpheus, ~22$/million
de caractères. Ajoute `AUDIO_TTS_ACTIF=true` (GROQ_API_KEY existe déjà
pour le chat). Si les deux sont configurés, Groq est utilisé en
priorité (meilleure fiabilité), Hugging Face reste le repli.

**Non testé en conditions réelles** pour le chemin Hugging Face (accès
au domaine bloqué depuis l'environnement de développement) : à
vérifier au premier vrai test.

## 7. Vidéo (le plus cher de loin -- à activer en dernier)

Ajoute `FAL_KEY` (compte fal.ai, modèle Wan 2.6) dans les variables
d'environnement Railway. ~0,05-0,07$/seconde, soit ~15-25x le coût
d'une image ou d'un message audio pour un contenu comparable -- à
activer seulement quand le budget le permet vraiment, pas en même
temps que les autres.

Particularité : contrairement à toutes les autres fonctionnalités, la
génération prend 1 à 3 minutes. Le flux est donc en 2 outils separes
(`lancer_generation_video` puis `consulter_statut_video`), jamais un
seul outil qui bloquerait l'agent en pleine conversation.

## 8. Modèles 3D

Réutilise `FAL_KEY` (même clé que la vidéo, section 7) : si l'une est
activée, l'autre l'est aussi automatiquement. ~0,225$/génération
(Hunyuan3D, endpoint "Rapid"), bien plus raisonnable que la vidéo.
Même flux en 2 temps (lancer/consulter) que la vidéo, pour la même
raison (pas instantané).

**Point à vérifier au premier vrai test** : le nom exact du champ
contenant l'URL du fichier `.glb` dans la réponse de fal.ai n'a pas pu
être confirmé à 100% par la documentation publique -- `generation_3d.py`
essaie plusieurs noms probables, et si aucun ne correspond, l'erreur
affichera la réponse brute pour ajuster en 1 ligne.

## Ce qui reste à faire ensuite (pas fait dans cette passe)

- Frontend : `lib/api.ts` (fonctions d'appel des 3 routes
  `/api/generation/...`) + affichage dans `BulleMessage.tsx` (carte de
  téléchargement pour PDF/zip, `<img>` inline pour les images) + boutons
  dans `BarreDeSaisie.tsx`.
