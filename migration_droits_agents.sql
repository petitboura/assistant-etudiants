-- Migration : système de droits par agent, 5 catégories
-- Logique : allow-list. Un agent n'a un outil QUE si le créateur l'a
-- coché explicitement ET que la plateforme le propose encore
-- maintenant (intersection calculée à chaque lecture, jamais une copie
-- figée -- un outil retiré côté plateforme disparaît automatiquement
-- de tous les agents sans rien toucher côté agent).

-- ============================================================
-- 1. REGISTRE PLATEFORME (source de vérité de ce qui EXISTE)
-- ============================================================
-- Une ligne par outil connu du système, tous serveurs confondus.
-- `disponible` reflète l'état réel (clé API présente, etc.), recalculé
-- à chaque déploiement ou par un job. C'est CE tableau que le
-- formulaire de création/modification d'agent lit pour savoir quelles
-- cases proposer -- jamais une liste écrite en dur côté frontend.
create table if not exists registre_outils_plateforme (
    nom_outil text primary key,
    categorie smallint not null check (categorie between 1 and 5),
    nom_serveur text not null,  -- 'generation' | 'github' | 'wolfram' | 'notion' | ...
    disponible boolean not null default true,
    updated_at timestamptz not null default now()
);

-- Seed initial (catégorie 1 : génération interne, granularité par outil)
insert into registre_outils_plateforme (nom_outil, categorie, nom_serveur) values
    ('generer_document', 1, 'generation'),
    ('generer_code', 1, 'generation'),
    ('chercher_fichier', 1, 'generation'),
    ('generer_site_zip', 1, 'generation'),
    ('generer_bundle', 1, 'generation'),
    ('exporter_donnees', 1, 'generation'),
    ('generer_image', 1, 'generation'),
    ('lancer_generation_3d', 1, 'generation'),
    ('consulter_statut_3d', 1, 'generation'),
    ('lancer_generation_video', 1, 'generation'),
    ('consulter_statut_video', 1, 'generation'),
    ('generer_audio', 1, 'generation'),
    ('envoyer_pour_signature', 1, 'generation'),
    ('consulter_statut_signature', 1, 'generation'),
    ('deployer_site', 1, 'generation'),
    -- Catégorie 2 : serveur externe global, sans connexion, granularité serveur
    ('serveur_wolfram', 2, 'wolfram'),
    ('serveur_github', 2, 'github'),
    -- Catégorie 3 : compte utilisateur final, granularité serveur
    ('serveur_notion', 3, 'notion')
on conflict (nom_outil) do nothing;

-- ============================================================
-- 2. DROITS PAR AGENT (ce que le créateur a coché)
-- ============================================================
-- Catégorie 1 : par outil individuel.
create table if not exists agents_outils_generation (
    agent_id uuid not null references agents(id) on delete cascade,
    nom_outil text not null references registre_outils_plateforme(nom_outil),
    primary key (agent_id, nom_outil)
);

-- Catégorie 2 et 3 : par serveur entier (remplace/complète agents.tools_enabled).
create table if not exists agents_serveurs (
    agent_id uuid not null references agents(id) on delete cascade,
    nom_serveur text not null,
    primary key (agent_id, nom_serveur)
);

-- Catégorie 4 : compte du créateur, connexion scopée à CET agent précis.
create table if not exists agents_connexions_createur (
    agent_id uuid not null references agents(id) on delete cascade,
    nom_serveur text not null,
    createur_id uuid not null references auth.users(id) on delete cascade,
    token_chiffre text,
    connecte_at timestamptz not null default now(),
    primary key (agent_id, nom_serveur)
);

-- Catégorie 5 : compte plateforme, une seule ligne par serveur, invisible
-- pour créateur/utilisateur, gérée uniquement par toi.
create table if not exists plateforme_connexions (
    nom_serveur text primary key,
    token_chiffre text,
    connecte_at timestamptz not null default now()
);

-- ============================================================
-- 3. NOTIFICATIONS : nouveaux types
-- ============================================================
-- Le CHECK existant sur notifications.type doit être étendu. Ajuste le
-- nom de contrainte si besoin (à vérifier dans ta migration d'origine).
alter table notifications drop constraint if exists notifications_type_check;
alter table notifications add constraint notifications_type_check
    check (type in (
        'follow', 'comment', 'rating', 'categorie_manquante', 'agent_update', 'feedback',
        'nouvel_outil_disponible',  -- catégorie 1/2/3 ajoutée par la plateforme, pour tout membre
        'outil_retire'              -- retiré par la plateforme, pour les créateurs concernés uniquement
    ));

-- Colonne pour rattacher une notif "nouvel_outil_disponible"/"outil_retire"
-- à l'outil concerné (pour construire le bouton d'action rapide).
alter table notifications add column if not exists nom_outil text
    references registre_outils_plateforme(nom_outil);
