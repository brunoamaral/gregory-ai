"""drf-spectacular postprocessing hooks."""

# Components whose serializers carry the opt-in ``editorial`` field.
_EDITORIAL_COMPONENTS = ("Article", "Trial", "TrialDetail")


def make_editorial_optional(result, generator, request, public):
	"""``editorial`` is a read-only SerializerMethodField, which drf-spectacular
	marks required. It is only present with ``?include=editorial`` (see
	api/editorial.py), so keep it documented but optional."""
	schemas = result.get("components", {}).get("schemas", {})
	for name in _EDITORIAL_COMPONENTS:
		required = schemas.get(name, {}).get("required")
		if required and "editorial" in required:
			required.remove("editorial")
	return result
