"""Check a version can list every check through the API and that each
check's badge URL is usable (no unset key rendered into it)."""
import sys
import django

django.setup()
from django.test import Client

from hc.accounts.models import Project

client = Client()
errors, n = [], 0
for project in Project.objects.exclude(api_key=""):
    r = client.get("/api/v3/checks/", HTTP_X_API_KEY=project.api_key)
    if r.status_code != 200:
        errors.append(f"list checks: HTTP {r.status_code}")
        continue
    for item in r.json()["checks"]:
        n += 1
        for key in ("badge_url", "ping_url"):
            if item.get(key) and "None" in item[key]:
                errors.append(f"{item.get('uuid') or item.get('name')}: {key} is {item[key]}")
if errors:
    sys.exit("\n".join(errors[:10]))
print(f"read {n} checks")
