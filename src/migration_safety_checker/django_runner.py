"""Test runner used by generated Django settings.

It combines ``SchemaCloneMixin`` with the project's own ``TEST_RUNNER``, which
the generated settings module passes in ``MSC_BASE_RUNNER``.
"""

import os

from django.utils.module_loading import import_string

from .django_support import SchemaCloneMixin

_Base = import_string(os.environ.get("MSC_BASE_RUNNER") or "django.test.runner.DiscoverRunner")


class Runner(SchemaCloneMixin, _Base):  # type: ignore[misc,valid-type]
    """The project's runner, pointed at an empty clone of the prepared schema."""
