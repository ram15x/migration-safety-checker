"""Make a healthchecks version write through its own code path: create a
project and check, then record a ping with a body over HTTP."""
import os, sys, uuid
import django

django.setup()
from django.contrib.auth.models import User
from django.test import Client

from hc.accounts.models import Profile, Project
from hc.api.models import Check, Ping

tag = uuid.uuid4().hex[:8]
user = User.objects.create(username=f"check-{tag}", email=f"{tag}@example.org")
Profile.objects.create(user=user)
project = Project.objects.create(owner=user, badge_key=tag, api_key=tag * 4)
check = Check.objects.create(project=project, name=f"check-{tag}")
resp = Client().post(f"/ping/{check.code}", data=f"written by {os.environ['MIGRATION_SAFETY_CHECKER_APP']} {tag}", content_type="text/plain")
if resp.status_code != 200:
    sys.exit(f"ping returned {resp.status_code}")
if not Ping.objects.filter(owner=check).exists():
    sys.exit("no ping row was stored")
print("wrote check", check.code)
