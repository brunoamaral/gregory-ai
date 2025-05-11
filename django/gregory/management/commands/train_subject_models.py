from django.core.management.base import BaseCommand, CommandError
import logging
from gregory.models import Team, Subject, PredictionRunLog
from django.utils import timezone
import argparse

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Train ML models for subjects. Supports filtering by team and subject.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--team',
            type=str,
            help='Team slug to filter subjects'
        )
        parser.add_argument(
            '--subject',
            type=str,
            help='Subject slug to train model for'
        )
        parser.add_argument(
            '--timeframe',
            type=int,
            default=90,
            help='Training timeframe in days (default: 90)'
        )
        parser.add_argument(
            '--device',
            type=str,
            default='cpu',
            choices=['cpu', 'gpu', 'tpu'],
            help='Device to use for training (default: cpu)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform a dry run without actually training models'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Increase output verbosity'
        )

    def log(self, message, level=logging.INFO, verbosity=1):
        """
        Log message based on verbosity level
        """
        if self.verbosity >= verbosity:
            self.stdout.write(message)
        logger.log(level, message)

    def handle(self, *args, **options):
        # Set verbosity level from options
        self.verbosity = 2 if options['verbose'] else 1
        dry_run = options['dry_run']
        team_slug = options['team']
        subject_slug = options['subject']
        timeframe = options['timeframe']
        device = options['device']

        # Log the parsed arguments
        self.log(f"Running with options:", verbosity=1)
        self.log(f"  Dry run: {dry_run}", verbosity=1)
        self.log(f"  Team: {team_slug or 'All teams'}", verbosity=1)
        self.log(f"  Subject: {subject_slug or 'All subjects'}", verbosity=1)
        self.log(f"  Timeframe: {timeframe} days", verbosity=1)
        self.log(f"  Device: {device}", verbosity=1)

        if dry_run:
            self.log("Dry run completed. No models were trained.", verbosity=1)
            return

        # Filter teams and subjects based on arguments
        teams = []
        if team_slug:
            try:
                team = Team.objects.get(slug=team_slug)
                teams = [team]
                self.log(f"Filtered to team: {team}", verbosity=2)
            except Team.DoesNotExist:
                raise CommandError(f"Team with slug '{team_slug}' does not exist")
        else:
            teams = Team.objects.all()
            self.log(f"Processing all {teams.count()} teams", verbosity=2)

        # Process each team
        for team in teams:
            self.log(f"Processing team: {team}", verbosity=1)
            
            subjects = []
            if subject_slug:
                try:
                    subject = Subject.objects.get(team=team, subject_slug=subject_slug)
                    subjects = [subject]
                    self.log(f"Filtered to subject: {subject}", verbosity=2)
                except Subject.DoesNotExist:
                    self.log(f"Subject with slug '{subject_slug}' does not exist for team {team}", level=logging.WARNING)
                    continue
            else:
                subjects = Subject.objects.filter(team=team)
                self.log(f"Processing {subjects.count()} subjects for team {team}", verbosity=2)

            # Process each subject
            for subject in subjects:
                # Here would be the actual model training code
                # For now, just log that we would train a model
                self.log(f"Would train model for subject: {subject.subject_name} (team: {team.name})", verbosity=1)
                
                # Create a log entry for this run - in a real implementation this would
                # happen after training completes successfully
                if not dry_run:
                    # This is just a placeholder - actual implementation would do the training
                    run_log = PredictionRunLog.objects.create(
                        team=team,
                        subject=subject,
                        model_version="v1.0.0",  # Should be dynamic
                        run_type="train",
                        triggered_by="management_command",
                        run_started=timezone.now()
                    )
                    # Simulate completion - in a real implementation this would happen after training
                    run_log.run_finished = timezone.now()
                    run_log.success = True
                    run_log.save()
                    
        self.log("Command completed successfully", verbosity=1)
