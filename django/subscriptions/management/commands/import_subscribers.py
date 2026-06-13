import csv
from django.core.management.base import BaseCommand, CommandError
from subscriptions.models import (
	Lists,
	Subscribers,
	ListSubscription,
	SubscriberSiteProfile,
)


class Command(BaseCommand):
	help = (
		"Import subscribers from a CSV file and add them to a list.\n\n"
		'Expected CSV columns: "file name", "email", "work email"[, "profile"]\n'
		'  - "file name"  : full name; first word → first_name, remainder → last_name\n'
		'  - "email"      : primary email (used first)\n'
		'  - "work email" : fallback if primary email is empty\n'
		'  - "profile"    : optional; prompted interactively when blank or absent\n\n'
		"If an email already exists in the database the row is skipped.\n"
		"The site is derived automatically from the list."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"csv_file",
			type=str,
			help="Path to the CSV file to import.",
		)
		parser.add_argument(
			"--list-id",
			type=int,
			required=True,
			dest="list_id",
			help="ID of the list to subscribe imported subscribers to.",
		)

	# ------------------------------------------------------------------
	# Helpers
	# ------------------------------------------------------------------

	def _prompt_profile(self, email):
		"""Interactively ask the operator to choose a profile for *email*."""
		options = SubscriberSiteProfile.PROFILEOPTIONS
		self.stdout.write(f"\nNo profile for {email!r}. Select a profile:")
		for i, (value, label) in enumerate(options, start=1):
			self.stdout.write(f"  {i}. {label}")
		while True:
			try:
				raw = input("Enter number: ").strip()
				idx = int(raw) - 1
				if 0 <= idx < len(options):
					return options[idx][0]
			except (ValueError, EOFError):
				pass
			self.stdout.write(
				self.style.WARNING(
					"  Invalid selection, please enter a number from the list above."
				)
			)

	# ------------------------------------------------------------------
	# Entry point
	# ------------------------------------------------------------------

	def handle(self, *args, **options):
		csv_path = options["csv_file"]
		list_id = options["list_id"]

		# Resolve list and derive site
		try:
			subscription_list = Lists.objects.select_related("site").get(
				list_id=list_id
			)
		except Lists.DoesNotExist:
			raise CommandError(f"List with ID {list_id} does not exist.")

		site = subscription_list.site
		self.stdout.write(
			f'Importing to list "{subscription_list.list_name}" (site: {site.domain})'
		)

		# Read CSV
		try:
			with open(csv_path, newline="", encoding="utf-8-sig") as fh:
				reader = csv.DictReader(fh)
				rows = list(reader)
		except FileNotFoundError:
			raise CommandError(f"File not found: {csv_path}")
		except OSError as exc:
			raise CommandError(f"Error reading CSV: {exc}")

		valid_profiles = {choice[0] for choice in SubscriberSiteProfile.PROFILEOPTIONS}

		imported = 0
		skipped = 0
		errors = 0

		for row in rows:
			full_name = (row.get("file name") or "").strip()
			email = (row.get("email") or "").strip()
			work_email = (row.get("work email") or "").strip()
			raw_profile = (row.get("profile") or "").strip().lower()

			# Determine which email to use
			if not email:
				email = work_email
			if not email:
				self.stdout.write(
					self.style.WARNING(f"  SKIP  – no email for row: {full_name!r}")
				)
				skipped += 1
				continue

			# Split full name: first word → first_name, rest → last_name
			name_parts = full_name.split(" ", 1)
			first_name = name_parts[0] if name_parts else ""
			last_name = name_parts[1] if len(name_parts) > 1 else ""

			# Determine profile
			if raw_profile in valid_profiles:
				profile = raw_profile
			else:
				if raw_profile:
					self.stdout.write(
						self.style.WARNING(
							f'  Unknown profile "{raw_profile}" for {email} – prompting.'
						)
					)
				profile = self._prompt_profile(email)

			# Skip if email already exists
			if Subscribers.objects.filter(email=email.lower()).exists():
				self.stdout.write(
					self.style.WARNING(f"  SKIP  – email already exists: {email}")
				)
				skipped += 1
				continue

			# Create subscriber
			try:
				subscriber = Subscribers.objects.create(
					first_name=first_name,
					last_name=last_name,
					email=email,
					active=True,
				)
			except Exception as exc:
				self.stdout.write(
					self.style.ERROR(
						f"  ERROR – could not create subscriber {email}: {exc}"
					)
				)
				errors += 1
				continue

			# Create list subscription
			ListSubscription.objects.create(
				subscriber=subscriber,
				list=subscription_list,
				consent_method="import",
			)

			# Create (or update) per-site profile
			SubscriberSiteProfile.objects.get_or_create(
				subscriber=subscriber,
				site=site,
				defaults={"profile": profile},
			)

			self.stdout.write(
				self.style.SUCCESS(
					f"  OK    – {email} ({first_name} {last_name}) [{profile}]"
				)
			)
			imported += 1

		self.stdout.write(
			self.style.SUCCESS(
				f"\nFinished. Imported: {imported}  |  Skipped: {skipped}  |  Errors: {errors}"
			)
		)
