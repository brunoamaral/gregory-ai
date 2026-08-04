from django.test import TestCase

from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema


class OpenAPISchemaGenerationTest(TestCase):
	"""Guards /api/schema/ against silent regressions.

	A view, filter, or serializer change that drf-spectacular can no longer
	introspect (missing serializer_class, an un-annotated SerializerMethodField,
	a django-filter field it can't resolve, ...) should fail CI here rather than
	ship a wrong or incomplete schema. Mirrors what
	``python manage.py spectacular --fail-on-warn`` checks.
	"""

	def setUp(self):
		# GENERATOR_STATS is a process-wide singleton that accumulates warnings
		# across every schema generation in the process; reset so this test
		# only sees warnings from its own run.
		GENERATOR_STATS.reset()

	def test_schema_generates_without_warnings_or_errors(self):
		generator = SchemaGenerator()
		schema = generator.get_schema(request=None, public=True)
		self.assertIsNotNone(schema)
		self.assertFalse(
			bool(GENERATOR_STATS),
			"drf-spectacular emitted warnings/errors during schema generation — "
			"run `python manage.py spectacular --fail-on-warn` locally to see "
			"them, then annotate the offending view/filter/serializer "
			"(@extend_schema, @extend_schema_field, help_text, ...).",
		)

	def test_schema_is_valid_openapi(self):
		generator = SchemaGenerator()
		schema = generator.get_schema(request=None, public=True)
		# Raises on structural violations (bad $refs, malformed parameter
		# objects, ...).
		validate_schema(schema)
