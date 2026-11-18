-- Contract: runs only after every old instance is gone.
DROP TRIGGER users_sync_names ON users;
DROP FUNCTION users_sync_names();
ALTER TABLE users DROP COLUMN first_name;
