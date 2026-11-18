"""Create a check through the public API, i.e. the version's own insert path."""
import json, os, sys, uuid
import django

django.setup()
from django.contrib.auth.models import User
from django.test import Client

from hc.accounts.models import Profile, Project

tag = uuid.uuid4().hex[:8]
user = User.objects.create(username=f"check-{tag}", email=f"{tag}@example.org")
Profile.objects.create(user=user)
Project.objects.create(owner=user, badge_key=tag, api_key=tag * 4)
r = Client().post("/api/v3/checks/", json.dumps({"name": f"by {os.environ['MIGRATION_SAFETY_CHECKER_APP']} {tag}"}), content_type="application/json", HTTP_X_API_KEY=tag * 4)
if r.status_code != 201:
    sys.exit(f"create check returned {r.status_code}: {r.content[:200]!r}")
print("created check", r.json().get("ping_url") or r.json().get("uuid"))
