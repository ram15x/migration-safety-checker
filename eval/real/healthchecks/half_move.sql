-- the 0108 data move interrupted halfway: even rows moved, odd rows not yet
UPDATE api_ping SET body_raw = convert_to(body, 'UTF8'), body = NULL
WHERE body IS NOT NULL AND id % 2 = 0;
