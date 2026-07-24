# Notifications push -- étapes manuelles + contrat frontend

Ajouté le 22/07/2026. Backend prêt (agent + planificateur de fond),
mais 3 choses restent à faire, dont une partie frontend obligatoire
(l'abonnement du navigateur ne peut pas se faire côté serveur).

## 1. Générer les clés VAPID (une seule fois)

```
pip install py-vapid --break-system-packages
python scripts/generer_cles_vapid.py
```

Copie les deux valeurs affichées dans les variables d'environnement
Railway : `VAPID_PRIVATE_KEY_PEM_B64` et `VAPID_PUBLIC_KEY`.

Tant que ces deux variables n'existent pas,
`notifications_push_disponible()` (core/notifications_push.py) renvoie
False : l'outil `planifier_rappel` n'est pas proposé à l'agent, et le
planificateur de fond (vérifie les rappels échus toutes les 60s) ne se
lance même pas.

## 2. Tables Supabase

Déjà créées (migration `notifications_push_tables` appliquée le
22/07/2026) : `abonnements_push` et `rappels`. Rien à faire ici.

## 3. Frontend -- CONTRAT EXACT à implémenter

C'est la seule partie qui ne peut pas être backend. 3 étapes côté
navigateur :

**a. Un service worker** (fichier JS servi à la racine du site, ex.
`/sw.js`) :
```javascript
self.addEventListener('push', (event) => {
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      data: { url: data.url },
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.notification.data?.url) {
    event.waitUntil(clients.openWindow(event.notification.data.url));
  }
});
```

**b. Demander la permission + s'abonner** (après connexion utilisateur) :
```javascript
const registration = await navigator.serviceWorker.register('/sw.js');
const permission = await Notification.requestPermission();
if (permission !== 'granted') return;

const { cle_publique } = await fetch('/api/notifications-push/cle-publique').then(r => r.json());

const subscription = await registration.pushManager.subscribe({
  userVisibleOnly: true,
  applicationServerKey: urlBase64ToUint8Array(cle_publique), // fonction utilitaire standard, largement documentée pour l'API Push
});

await fetch('/api/notifications-push/abonnement', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
  body: JSON.stringify(subscription.toJSON()),
});
```

**c. Désabonnement** (paramètres utilisateur, si on veut couper les
notifications) : `POST /api/notifications-push/desabonnement` avec
`{"endpoint": subscription.endpoint}`.

## Ce que l'agent peut déjà faire (une fois les clés VAPID configurées)

Outil `planifier_rappel(contenu, dans_minutes)` : ex. étudiant dit
"préviens-moi dans 3 jours de réviser mon contrôle", l'agent appelle
l'outil avec `dans_minutes=4320`. Le planificateur de fond (dans
`api/main.py`) vérifie toutes les 60s et envoie la notification au bon
moment à tous les appareils abonnés de cet utilisateur.

## Réutilisable pour un événement système (pas encore branché)

`envoyer_notification_push(user_id, titre, corps, url)`
(core/notifications_push.py) est appelable directement par n'importe
quel autre module -- ex. prévenir quand une signature Lumin est
confirmée, ou qu'une vidéo est prête. Pas câblé automatiquement dans
cette passe (aurait demandé de modifier signature/vidéo en plus),
mais la fonction est prête si tu veux l'ajouter.

## Point d'incertitude à vérifier au premier test réel

`planifier_rappel` récupère l'identité de l'utilisateur via
`ctx.request_context.request.query_params` (voir
core/serveur_mcp_generation.py) -- mécanisme FastMCP standard pour ce
cas, mais jamais testé en conditions réelles dans ce projet. Si l'outil
échoue avec "impossible d'identifier l'utilisateur", c'est le premier
endroit à regarder.
