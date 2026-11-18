"""Check a healthchecks version can read every ping already in the database,
through the model layer and through the API endpoints that serve ping bodies."""
import sys
import django

django.setup()
from django.test import Client

from hc.api.models import Check, Ping

errors = []
n = 0
for ping in Ping.objects.all():
    n += 1
    try:
        ping.to_dict()
        ping.get_body()
        ping.has_body()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ping {ping.id}: {type(exc).__name__}: {exc}")
        break

client = Client()
for check in Check.objects.select_related("project"):
    key = check.project.api_key
    if not key:
        continue
    r = client.get(f"/api/v3/checks/{check.code}/pings/", HTTP_X_API_KEY=key)
    if r.status_code != 200:
        errors.append(f"list pings for {check.code}: HTTP {r.status_code}")
        continue
    for item in r.json()["pings"]:
        if item.get("body_url"):
            b = client.get(item["body_url"].replace("http://localhost:8000", ""), HTTP_X_API_KEY=key)
            if b.status_code != 200:
                errors.append(f"body {item['body_url']}: HTTP {b.status_code}")
if errors:
    sys.exit("\n".join(errors[:10]))
print(f"read {n} pings")
