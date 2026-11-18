"""Settings overlay used by Migration Safety Checker for healthchecks runs."""
from hc.settings import *  # noqa: F401,F403
from migration_safety_checker.django_support import database_from_env

DATABASES = {"default": database_from_env()}
TEST_RUNNER = "msc_runner.Runner"
ALLOWED_HOSTS = ["*"]
