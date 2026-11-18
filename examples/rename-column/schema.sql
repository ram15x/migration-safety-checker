CREATE TABLE users (
    id         serial PRIMARY KEY,
    first_name text NOT NULL,
    last_name  text NOT NULL,
    email      text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO users (first_name, last_name, email) VALUES
    ('Grace',  'Hopper',   'grace@example.com'),
    ('Alan',   'Turing',   'alan@example.com'),
    ('Katherine', 'Johnson', 'katherine@example.com');
