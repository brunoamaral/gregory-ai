"""Author matching shared by get_authors (CrossRef) and the PubMed record writer."""

from gregory.models import Authors

CREATED = "created"
UPDATED = "updated"
EXISTING = "existing"
AMBIGUOUS = "ambiguous"


def get_or_create_author(given_name, family_name, orcid=None):
	"""Find or create the Authors row for a name (and ORCID, when known).

	Returns ``(author, status)``. Matching is ORCID first: an ORCID match is the
	author, and a changed name is written onto it (status ``updated``). Without an
	ORCID the match is by exact given and family name. Two rows with the same name
	can't be told apart, so the result is ``(None, "ambiguous")``.

	Callers pass non-empty names and an already normalised ORCID.
	"""
	if orcid:
		author, created = Authors.objects.get_or_create(
			ORCID=orcid,
			defaults={"given_name": given_name, "family_name": family_name},
		)
		if created:
			return author, CREATED
		if author.given_name != given_name or author.family_name != family_name:
			author.given_name = given_name
			author.family_name = family_name
			author.save()
			return author, UPDATED
		return author, EXISTING

	try:
		return Authors.objects.get(given_name=given_name, family_name=family_name), EXISTING
	except Authors.DoesNotExist:
		return (
			Authors.objects.create(
				given_name=given_name, family_name=family_name, ORCID=orcid
			),
			CREATED,
		)
	except Authors.MultipleObjectsReturned:
		return None, AMBIGUOUS
