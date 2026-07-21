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

## 4. Génération d'images (payant, en attente de budget)

Ajoute simplement `TOGETHER_API_KEY` dans les variables d'environnement
Railway. Rien d'autre à changer : `image_generation_disponible()`
(core/generation_images.py) la détecte automatiquement au prochain
démarrage du process, et l'outil `generer_image` apparaît alors tout
seul dans la liste proposée à l'agent, plus le bouton frontend cesse de
renvoyer "pas encore disponible" (une fois le frontend branché --
prochaine étape, pas encore fait).

## 5. Signature électronique (gratuit jusqu'à 5/mois, aucune urgence budget)

Contrairement à Together AI, Lumin (developers.luminpdf.com) est
gratuit jusqu'à 5 signatures/mois, donc rien n'empêche de l'activer dès
maintenant :
1. Connecte-toi sur Lumin -> Settings -> Developer settings -> API keys
   -> Generate key
2. Ajoute `LUMIN_API_KEY` dans les variables d'environnement Railway
Même détection automatique que pour Together AI (`signature_disponible()`
dans core/generation_signature.py).

## 6. Audio / synthèse vocale (via Groq, clé déjà présente -- interrupteur séparé)

Contrairement aux autres fonctionnalités, `GROQ_API_KEY` existe déjà
dans ce projet (utilisée pour le chat). Le gate n'est donc PAS la
présence de la clé, mais un interrupteur dédié à ajouter dans les
variables d'environnement Railway :

```
AUDIO_TTS_ACTIF=true
```

Tant que cette variable n'existe pas (ou vaut autre chose que "true"),
`audio_disponible()` (core/generation_audio.py) renvoie False, même si
GROQ_API_KEY est déjà là pour le chat. Coût indicatif : ~22$/million de
caractères (modèle Orpheus, statut "Preview" chez Groq au 20/07/2026),
à comparer aux ~0,003$/image pour Together AI.

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
