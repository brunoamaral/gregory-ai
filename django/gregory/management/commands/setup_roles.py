"""
Sync the Editor group to the role defined in gregory/roles.py, and report
staff accounts that don't fit it.

The group is set to exactly the role's permissions: missing ones are added,
anything else is removed. Run it after every deploy that changes
gregory/roles.py. With --check nothing is written, and it exits non-zero if
the group has drifted from the role.

It also lists active non-superuser staff accounts that:
  - are not on any active team (they can see their organisation but edit
    nothing: editing is limited to the user's teams, see gregory/team_scope.py);
  - hold permissions directly on the account rather than through a group
    (those bypass the role: clear them in the user admin and add the person
    to the Editor group instead);
  - are not in the Editor group.

Usage:
  docker exec gregory python manage.py setup_roles
  docker exec gregory python manage.py setup_roles --check
"""

import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from gregory.models import Team
from gregory.roles import EDITOR_GROUP, editor_group_drift, sync_editor_group


class Command(BaseCommand):
	help = "Sync the Editor group to gregory/roles.py and report staff accounts that don't fit it."

	def add_arguments(self, parser):
		parser.add_argument(
			"--check",
			action="store_true",
			help="Report only; exit 1 if the Editor group differs from the role.",
		)

	def handle(self, *args, **options):
		try:
			drifted = self._sync_or_check(options["check"])
		except LookupError as exc:
			# A permission the role names doesn't exist: a model was renamed, or
			# migrations haven't run yet.
			raise CommandError(str(exc)) from exc
		self._report_staff()
		if drifted:
			sys.exit(1)

	def _sync_or_check(self, check):
		drifted = False
		if check:
			missing, extra = editor_group_drift()
			drifted = bool(missing or extra)
			if drifted:
				self.stdout.write(self.style.WARNING(f'The "{EDITOR_GROUP}" group differs from the role:'))
				for name in missing:
					self.stdout.write(f"  missing  {name}")
				for name in extra:
					self.stdout.write(f"  extra    {name}")
			else:
				self.stdout.write(self.style.SUCCESS(f'The "{EDITOR_GROUP}" group matches the role.'))
		else:
			group, added, removed = sync_editor_group()
			for name in added:
				self.stdout.write(f"  added    {name}")
			for name in removed:
				self.stdout.write(f"  removed  {name}")
			self.stdout.write(
				self.style.SUCCESS(
					f'The "{EDITOR_GROUP}" group holds {group.permissions.count()} permissions '
					f"({len(added)} added, {len(removed)} removed)."
				)
			)
		return drifted

	def _report_staff(self):
		staff = (
			get_user_model()
			.objects.filter(is_staff=True, is_superuser=False, is_active=True)
			.annotate(direct_perms=Count("user_permissions", distinct=True))
			.prefetch_related("groups")
			.order_by("username")
		)
		# Active teams only: a team that's been deactivated doesn't count.
		on_a_team = set(
			Team.objects.filter(members__isnull=False).values_list("members", flat=True)
		)

		no_team, direct, not_editor = [], [], []
		for user in staff:
			if user.pk not in on_a_team:
				no_team.append(user.username)
			if user.direct_perms:
				direct.append(f"{user.username} ({user.direct_perms})")
			if EDITOR_GROUP not in {g.name for g in user.groups.all()}:
				not_editor.append(user.username)

		self.stdout.write("")
		self.stdout.write(f"Active non-superuser staff accounts: {len(staff)}")
		self._list("Not on any team (read-only)", no_team)
		self._list("Permissions held directly on the account", direct)
		self._list(f'Not in the "{EDITOR_GROUP}" group', not_editor)

	def _list(self, title, names):
		if not names:
			self.stdout.write(f"{title}: none")
			return
		self.stdout.write(self.style.WARNING(f"{title}: {len(names)}"))
		for name in names:
			self.stdout.write(f"  {name}")
