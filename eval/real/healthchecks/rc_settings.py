"""Settings overlay used by ReleaseCompat for healthchecks runs."""
from hc.settings import *  # noqa: F401,F403
from releasecompat.django_support import database_from_env

DATABASES = {"default": database_from_env()}
TEST_RUNNER = "rc_runner.Runner"
ALLOWED_HOSTS = ["*"]
