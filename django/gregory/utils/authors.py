"""Author matching shared by get_authors (CrossRef) and the PubMed record writer."""

from gregory.models import Authors
from gregory.utils.author_names import name_key

CREATED = "created"
UPDATED = "updated"
EXISTING = "existing"
AMBIGUOUS = "ambiguous"


def find_authors_by_name(given_name, family_name):
	"""Existing no-ORCID matches for a name, as a list.

	Exact given and family name first; the caller decides what several exact matches
	mean. If none, the one row whose ``name_key`` equals the name's key (accents,
	case, dots and hyphens ignored). Several rows on the key is not a match: the
	caller creates a row, as it did before ``name_key`` existed, rather than dropping
	the author from the article. A name whose key is empty never matches on it.
	"""
	exact = list(Authors.objects.filter(given_name=given_name, family_name=family_name).order_by("pk"))
	if exact:
		return exact
	key = name_key(given_name, family_name)
	if not key:
		return []
	loose = list(Authors.objects.filter(name_key=key).order_by("pk")[:2])
	return loose if len(loose) == 1 else []


def get_or_create_author(given_name, family_name, orcid=None):
	"""Find or create the Authors row for a name (and ORCID, when known).

	Returns ``(author, status)``. Matching is ORCID first: an ORCID match is the
	author, and a changed name is written onto it (status ``updated``). Without an
	ORCID the match is by exact given and family name, then by ``name_key``. Several
	rows matching can't be told apart, so the result is ``(None, "ambiguous")``.

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

	matches = find_authors_by_name(given_name, family_name)
	if len(matches) == 1:
		return matches[0], EXISTING
	if len(matches) > 1:
		return None, AMBIGUOUS
	return (
		Authors.objects.create(
			given_name=given_name, family_name=family_name, ORCID=orcid
		),
		CREATED,
	)
