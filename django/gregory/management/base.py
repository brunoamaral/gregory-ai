from django.core.management.base import BaseCommand


class GregoryBaseCommand(BaseCommand):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.verbosity = 1

	def execute(self, *args, **options):
		self.verbosity = options.get("verbosity", 1)
		return super().execute(*args, **options)

	def log(self, message, level=2, style_func=None):
		"""
		Log a message if the verbosity level is high enough.

		Levels:
		0 = Silent
		1 = Only main processing steps (feeds, sources)
		2 = Detailed information (default for most messages)
		3 = Debug information
		"""
		if self.verbosity >= level:
			if style_func:
				self.stdout.write(style_func(message))
			else:
				self.stdout.write(message)
