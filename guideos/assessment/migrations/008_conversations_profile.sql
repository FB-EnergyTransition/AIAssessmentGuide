-- Conversations reference the participant's profile of the domain instead of a session.
-- Existing pilot conversations are purged before this rolls out (purge_data --confirm).
ALTER TABLE conversations RENAME COLUMN assessment_session_id TO profile_id;
