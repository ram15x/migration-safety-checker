-- Renames the column in one step. Old instances still query first_name.
ALTER TABLE users RENAME COLUMN first_name TO given_name;
