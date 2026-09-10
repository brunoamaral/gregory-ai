"""
Test helpers for site-scoped API visibility.

Under subject scoping a caller sees a row only when one of its subjects sits
in the ``scope_subjects`` of a site that caller can reach. That makes two
things load-bearing in a fixture which used not to be:

  1. content must carry a subject at all, and
  2. some site must publish that subject.

Before Phase 4, ``OrganizationApiSettings.make_api_public = True`` was the
whole story, so most fixtures in this suite set that and stopped. These
helpers are the equivalent statement in the new model, kept in one place so
that a change to the visibility rule is one edit rather than sixty.

Typical use, at the end of a ``setUpTestData``::

    cls.org = Organization.objects.create(name="Org", slug="org")
    cls.team = Team.objects.create(organization=cls.org, name="T", slug="t")
    cls.subject = Subject.objects.create(
        subject_name="S", subject_slug="s", team=cls.team
    )
    article.subjects.add(cls.subject)
    publish_subjects(cls.subject, organization=cls.org)

``publish_subjects`` is for content that should be publicly readable. Use
``private_site_publishing`` when a test needs a scope that an anonymous
caller must NOT see -- that is the shape a second tenant has, and asserting
against it is how these tests show isolation rather than assuming it.
"""

from django.contrib.sites.models import Site

from gregory.models import OrganizationSite
from sitesettings.models import CustomSetting

# Domains are generated rather than fixed so that two helper calls in one
# test (a public site and a private one, say) cannot collide on Site.domain.
_counter = 0


def _next_domain(prefix):
	global _counter
	_counter += 1
	return f"{prefix}-{_counter}.test.example.com"


def _make_site(*subjects, api_public, organization=None, domain=None, name=None):
	site = Site.objects.create(
		domain=domain or _next_domain("public" if api_public else "private"),
		name=name or ("Public test site" if api_public else "Private test site"),
	)
	settings_row = CustomSetting.objects.create(
		site=site,
		title=name or f"{site.domain} settings",
		api_public=api_public,
	)
	for subject in subjects:
		settings_row.scope_subjects.add(subject)
	if organization is not None:
		# What visible_subject_ids walks for a signed-in member, and what it
		# checks a site-bound API key against. Without it an authenticated
		# caller sees nothing even though their organisation owns the team.
		#
		# is_default only on the FIRST site an organisation gets: there is a
		# UniqueConstraint(organization, condition=is_default=True), and a
		# fixture that gives one organisation two sites -- a base fixture plus
		# a subclass adding its own, which is the normal shape here -- would
		# otherwise fail on the second with an IntegrityError that says
		# nothing about the real cause.
		already_has_default = OrganizationSite.objects.filter(
			organization=organization, is_default=True
		).exists()
		OrganizationSite.objects.get_or_create(
			organization=organization,
			site=site,
			defaults={"is_default": not already_has_default},
		)
	return site


def publish_subjects(*subjects, organization=None, domain=None, name=None):
	"""Make ``subjects`` readable by an anonymous caller.

	Creates a Site with ``api_public=True`` whose scope is exactly those
	subjects. Pass ``organization`` to also link the site to an organisation,
	which is what an authenticated member or a site-bound API key resolves
	through.

	Returns the Site, so a test that needs the id (``?site_id=``, an API key
	binding) can use it.
	"""
	return _make_site(
		*subjects,
		api_public=True,
		organization=organization,
		domain=domain,
		name=name,
	)


def private_site_publishing(*subjects, organization=None, domain=None, name=None):
	"""The counterpart: a site whose scope an anonymous caller cannot see.

	``api_public=False``, so these subjects are reachable only through a key
	bound to this site or a user in ``organization``. Use it for the "other
	tenant" half of an isolation assertion.
	"""
	return _make_site(
		*subjects,
		api_public=False,
		organization=organization,
		domain=domain,
		name=name,
	)
