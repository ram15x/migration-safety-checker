"""Seed the baseline with data created through the old version's models,
including legacy ping rows that still keep their body in the old column."""
import uuid
import django

django.setup()
from django.contrib.auth.models import User
from django.db import connection

from hc.accounts.models import Profile, Project
from hc.api.models import Check, Ping

user = User.objects.create(username="seed", email="seed@example.org")
Profile.objects.create(user=user)
project = Project.objects.create(owner=user, badge_key="seed", api_key="s" * 32)
for i in range(6):
    check = Check.objects.create(project=project, name=f"seed-{i}")
    for n in range(1, 5):
        check.ping("1.2.3.4", "http", "post", "ua", f"body {i}-{n}".encode(), "success", None)
with connection.cursor() as cur:  # legacy rows: text body, no raw body
    cur.execute("UPDATE api_ping SET body = convert_from(body_raw, 'UTF8'), body_raw = NULL WHERE n % 2 = 0 AND body_raw IS NOT NULL")
print("seeded", Ping.objects.count(), "pings")
