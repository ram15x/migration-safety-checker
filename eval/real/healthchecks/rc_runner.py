"""healthchecks' own test runner with the schema-clone behaviour added."""
from hc.api.tests import CustomRunner
from releasecompat.django_support import SchemaCloneMixin


class Runner(SchemaCloneMixin, CustomRunner):
    pass
