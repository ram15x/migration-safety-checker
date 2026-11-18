-- the 0117 backfill interrupted halfway: even ids filled, odd ids not yet
UPDATE api_check SET badge_key = gen_random_uuid() WHERE badge_key IS NULL AND id % 2 = 0;
