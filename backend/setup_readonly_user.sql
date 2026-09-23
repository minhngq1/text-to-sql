-- Creates a PostgreSQL user with read-only access for the Text-to-SQL app.
--
-- This is the 2nd defense layer in the architecture: even if the validation
-- layer in backend/security.py is bypassed, PostgreSQL still rejects any write.
--
-- How to run (as an admin account, only needs to run once):
--     psql -U postgres -d do_an -f setup_readonly_user.sql
--
-- After running, update backend/.env:
--     DB_USER=textsql_readonly
--     DB_PASSWORD=<the password you set on the CREATE ROLE line below>


CREATE ROLE textsql_readonly WITH LOGIN PASSWORD '<set_your_password_here>';

-- Disallow creating new tables in the public schema.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM textsql_readonly;

GRANT CONNECT ON DATABASE do_an TO textsql_readonly;
GRANT USAGE ON SCHEMA public TO textsql_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO textsql_readonly;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO textsql_readonly;

-- Tables created later are also read-only by default.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO textsql_readonly;

-- Block file-reading and system functions at the privilege level, not just the app level.
REVOKE EXECUTE ON FUNCTION pg_read_file(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_read_binary_file(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_ls_dir(text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_sleep(double precision) FROM PUBLIC;

-- Sanity check: the query below must return false in all three columns.
SELECT
    rolsuper      AS is_superuser,
    rolcreatedb   AS can_create_db,
    rolcreaterole AS can_create_role
FROM pg_roles
WHERE rolname = 'textsql_readonly';
