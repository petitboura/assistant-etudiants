-- Corrige oauth_temp : la table ne contenait que (state, code_verifier,
-- created_at), alors que connexions/oauth_generique.py (demarrer_connexion,
-- etat_en_attente, finaliser_connexion) écrit et lit AUSSI service, user_id
-- et agent_id depuis le tout début de ce moteur générique.
--
-- Conséquence AVANT ce fix : TOUTE tentative de connexion OAuth via ce
-- moteur générique (donc GitHub, premier service à l'utiliser réellement)
-- échouait silencieusement à l'écriture, avec en logs Railway :
--   PGRST204 "Could not find the 'agent_id' column of 'oauth_temp' in
--   the schema cache"
-- Confirmé en test réel le 2026-07-23 : ce n'était pas un problème de
-- configuration (GITHUB_CLIENT_ID etc. étaient bien présents sur
-- Railway), mais un vrai manque de migration jamais appliqué.
--
-- Déjà appliqué en direct sur Supabase (rwcyeppxfonvqbvztxyg) le
-- 2026-07-23 -- ce fichier documente le changement dans le repo, pas une
-- migration encore à exécuter.

ALTER TABLE oauth_temp
  ADD COLUMN IF NOT EXISTS service text,
  ADD COLUMN IF NOT EXISTS user_id uuid,
  ADD COLUMN IF NOT EXISTS agent_id text;
