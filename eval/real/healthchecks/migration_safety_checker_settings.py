"""Settings overlay used by migration-safety-checker for healthchecks runs."""
from hc.settings import *  # noqa: F401,F403
from migration_safety_checker.django_support import database_from_env

DATABASES = {"default": database_from_env()}
TEST_RUNNER = "migration_safety_checker_runner.Runner"
ALLOWED_HOSTS = ["*"]
