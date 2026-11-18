-- Expand: add the new column, keep both in sync, copy existing data.
ALTER TABLE users ADD COLUMN given_name text;

CREATE FUNCTION users_sync_names() RETURNS trigger AS $$
BEGIN
    IF NEW.given_name IS NULL THEN
        NEW.given_name := NEW.first_name;
    END IF;
    IF NEW.first_name IS NULL THEN
        NEW.first_name := NEW.given_name;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER users_sync_names
    BEFORE INSERT OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION users_sync_names();

UPDATE users SET given_name = first_name WHERE given_name IS NULL;
