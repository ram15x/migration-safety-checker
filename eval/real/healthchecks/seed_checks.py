"""Seed the v3.9 baseline through its own API: checks created by the old
version, so their badge_key is unset, plus a few whose badge URL was requested."""
import json
import django

django.setup()
from django.contrib.auth.models import User
from django.test import Client

from hc.accounts.models import Profile, Project
from hc.api.models import Check

user = User.objects.create(username="seed", email="seed@example.org")
Profile.objects.create(user=user)
Project.objects.create(owner=user, badge_key="seed", api_key="s" * 32)
client = Client()
for i in range(8):
    r = client.post("/api/v3/checks/", json.dumps({"name": f"seed-{i}"}), content_type="application/json", HTTP_X_API_KEY="s" * 32)
    assert r.status_code == 201, r.content
for c in Check.objects.all()[:3]:  # badge URL requested: old version fills the key lazily
    if hasattr(c, "prepare_badge_key"):
        c.prepare_badge_key()
        c.save()
print("seeded", Check.objects.count(), "checks")
