"""
Service logic for reassigning all objects from one team to another.

Used by both the admin action and the ``reassign_team_objects`` management command.
"""
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, List

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

ConflictMode = Literal['skip', 'rename', 'merge']


@dataclass
class ReassignmentReport:
	"""Collects what happened (or would happen) during a reassignment."""
	subjects_moved: List[str] = field(default_factory=list)
	subjects_skipped: List[str] = field(default_factory=list)
	subjects_renamed: List[str] = field(default_factory=list)
	subjects_merged: List[str] = field(default_factory=list)
	sources_moved: int = 0
	categories_moved: int = 0
	lists_moved: int = 0
	prediction_logs_moved: int = 0
	articles_relinked: int = 0
	trials_relinked: int = 0
	model_dirs_moved: List[str] = field(default_factory=list)
	model_dirs_failed: List[str] = field(default_factory=list)
	errors: List[str] = field(default_factory=list)

	def summary(self) -> str:
		lines = [
			f"Subjects moved:      {len(self.subjects_moved)}",
			f"Subjects skipped:    {len(self.subjects_skipped)}",
			f"Subjects renamed:    {len(self.subjects_renamed)}",
			f"Subjects merged:     {len(self.subjects_merged)}",
			f"Sources moved:       {self.sources_moved}",
			f"Categories moved:    {self.categories_moved}",
			f"Lists moved:         {self.lists_moved}",
			f"Prediction logs:     {self.prediction_logs_moved}",
			f"Articles relinked:   {self.articles_relinked}",
			f"Trials relinked:     {self.trials_relinked}",
			f"Model dirs moved:    {len(self.model_dirs_moved)}",
			f"Model dirs failed:   {len(self.model_dirs_failed)}",
		]
		if self.errors:
			lines.append("Errors:")
			for e in self.errors:
				lines.append(f"  - {e}")
		return "\n".join(lines)


def _move_model_dir(from_slug: str, to_slug: str, report: ReassignmentReport, dry_run: bool) -> None:
	"""
	Move the on-disk model directory from models/<from_slug>/ to models/<to_slug>/.

	The directory structure under BASE_DIR/models/ follows:
	  <team_slug>/<subject_slug>/<algorithm>/<version>/

	We move the entire team-level directory (or merge into an existing one).
	"""
	base_dir = Path(settings.BASE_DIR) / "models"
	src = base_dir / from_slug
	dst = base_dir / to_slug

	if not src.exists():
		logger.debug("No model directory found at %s — nothing to move.", src)
		return

	if dry_run:
		report.model_dirs_moved.append(f"{src} → {dst} (dry run)")
		return

	try:
		if not dst.exists():
			dst.mkdir(parents=True, exist_ok=True)

		# Walk subjects inside src and move/merge each one.
		for subject_dir in src.iterdir():
			if not subject_dir.is_dir():
				continue
			dst_subject = dst / subject_dir.name
			if dst_subject.exists():
				# Merge: walk algorithms
				for algo_dir in subject_dir.iterdir():
					if not algo_dir.is_dir():
						continue
					dst_algo = dst_subject / algo_dir.name
					dst_algo.mkdir(parents=True, exist_ok=True)
					for version_dir in algo_dir.iterdir():
						dst_version = dst_algo / version_dir.name
						if dst_version.exists():
							report.model_dirs_failed.append(
								f"Version dir already exists, skipped: {dst_version}"
							)
							logger.warning("Version dir already exists, skipping: %s", dst_version)
						else:
							shutil.move(str(version_dir), str(dst_version))
							report.model_dirs_moved.append(f"{version_dir} → {dst_version}")
			else:
				shutil.move(str(subject_dir), str(dst_subject))
				report.model_dirs_moved.append(f"{subject_dir} → {dst_subject}")

		# Remove the source team dir if it's now empty.
		remaining = list(src.iterdir())
		if not remaining:
			src.rmdir()
	except Exception as exc:
		msg = f"Failed to move model directory {src} → {dst}: {exc}"
		report.model_dirs_failed.append(msg)
		report.errors.append(msg)
		logger.error(msg)


def _merge_subjects(source_subject, target_subject, dry_run: bool, report: ReassignmentReport) -> None:
	"""
	Merge source_subject into target_subject.

	Moves:
	- Sources (FK: sources.subject — has PROTECT on Source→Subject; update FK first)
	- MLPredictions (unique per article+subject+model_version+algorithm — skip duplicates)
	- ArticleSubjectRelevance (unique per article+subject — skip duplicates)
	- PredictionRunLog (via subject FK — reassigned to target_subject)
	- M2M: Articles↔Subject, Trials↔Subject, TeamCategory↔Subject, Lists↔Subject

	Then deletes source_subject.
	"""
	from gregory.models import Sources, MLPredictions, ArticleSubjectRelevance, PredictionRunLog

	if dry_run:
		return

	# Sources — update FK (safe because PROTECT is on Source.subject, not the other way)
	Sources.objects.filter(subject=source_subject).update(subject=target_subject)

	# MLPredictions — skip duplicates (unique_article_subject_prediction constraint)
	for pred in MLPredictions.objects.filter(subject=source_subject):
		exists = MLPredictions.objects.filter(
			article=pred.article,
			subject=target_subject,
			model_version=pred.model_version,
			algorithm=pred.algorithm,
		).exists()
		if not exists:
			pred.subject = target_subject
			pred.save(update_fields=['subject'])
		else:
			pred.delete()

	# ArticleSubjectRelevance — skip duplicates
	for asr in ArticleSubjectRelevance.objects.filter(subject=source_subject):
		exists = ArticleSubjectRelevance.objects.filter(
			article=asr.article,
			subject=target_subject,
		).exists()
		if not exists:
			asr.subject = target_subject
			asr.save(update_fields=['subject'])
		else:
			asr.delete()

	# PredictionRunLog
	PredictionRunLog.objects.filter(subject=source_subject).update(subject=target_subject)

	# M2M: Articles
	for article in source_subject.articles.all():
		if not target_subject.articles.filter(pk=article.pk).exists():
			target_subject.articles.add(article)
		source_subject.articles.remove(article)

	# M2M: Trials
	for trial in source_subject.trials.all():
		if not target_subject.trials.filter(pk=trial.pk).exists():
			target_subject.trials.add(trial)
		source_subject.trials.remove(trial)

	# M2M: TeamCategory subjects
	for category in source_subject.team_subjects.all():
		if not category.subjects.filter(pk=target_subject.pk).exists():
			category.subjects.add(target_subject)
		category.subjects.remove(source_subject)

	# M2M: Lists subjects
	from subscriptions.models import Lists
	for lst in Lists.objects.filter(subjects=source_subject):
		if not lst.subjects.filter(pk=target_subject.pk).exists():
			lst.subjects.add(target_subject)
		lst.subjects.remove(source_subject)

	# Now it's safe to delete the source subject
	source_subject.hard_delete() if hasattr(source_subject, 'hard_delete') else source_subject.delete()


def reassign_team(
	from_team,
	to_team,
	conflict: ConflictMode = 'skip',
	dry_run: bool = False,
) -> ReassignmentReport:
	"""
	Reassign all objects that belong to ``from_team`` to ``to_team``.

	Both teams must belong to the same organisation.

	Args:
		from_team: Team instance (may be inactive)
		to_team:   Team instance (must be active)
		conflict:  How to handle Subject slug collisions — 'skip', 'rename', or 'merge'
		dry_run:   If True, compute and return the report without writing anything.

	Returns:
		ReassignmentReport
	"""
	from gregory.models import Subject, Sources, TeamCategory, PredictionRunLog

	report = ReassignmentReport()

	if from_team.organization_id != to_team.organization_id:
		raise ValueError(
			f"Teams must belong to the same organisation. "
			f"'{from_team}' is in '{from_team.organization}', "
			f"'{to_team}' is in '{to_team.organization}'."
		)

	if not to_team.is_active:
		raise ValueError(f"Target team '{to_team}' is inactive. Choose an active team.")

	# ------------------------------------------------------------------ #
	# 1. Subjects                                                          #
	# ------------------------------------------------------------------ #
	existing_slugs = set(
		Subject.objects.filter(team=to_team).values_list('subject_slug', flat=True)
	)

	for subject in Subject.objects.filter(team=from_team):
		if subject.subject_slug not in existing_slugs:
			if not dry_run:
				subject.team = to_team
				subject.save(update_fields=['team'])
			report.subjects_moved.append(subject.subject_slug)
			existing_slugs.add(subject.subject_slug)
		else:
			# Conflict!
			if conflict == 'skip':
				report.subjects_skipped.append(subject.subject_slug)
				logger.info("Skipping subject '%s' — slug already exists in target team.", subject.subject_slug)

			elif conflict == 'rename':
				new_slug = f"{subject.subject_slug}-from-{from_team.slug}"
				# Ensure the new slug is also unique
				counter = 2
				candidate = new_slug
				while candidate in existing_slugs:
					candidate = f"{new_slug}-{counter}"
					counter += 1
				new_slug = candidate
				if not dry_run:
					subject.subject_slug = new_slug
					subject.team = to_team
					subject.save(update_fields=['subject_slug', 'team'])
				report.subjects_renamed.append(f"{subject.subject_slug} → {new_slug}")
				existing_slugs.add(new_slug)

			elif conflict == 'merge':
				target_subject = Subject.objects.get(team=to_team, subject_slug=subject.subject_slug)
				_merge_subjects(subject, target_subject, dry_run=dry_run, report=report)
				report.subjects_merged.append(subject.subject_slug)

	# ------------------------------------------------------------------ #
	# 2. Sources (those not already moved via Subject merge)               #
	# ------------------------------------------------------------------ #
	sources_qs = Sources.objects.filter(team=from_team)
	report.sources_moved = sources_qs.count()
	if not dry_run:
		sources_qs.update(team=to_team)

	# ------------------------------------------------------------------ #
	# 3. TeamCategories                                                    #
	# ------------------------------------------------------------------ #
	categories_qs = TeamCategory.objects.filter(team=from_team)
	report.categories_moved = categories_qs.count()
	if not dry_run:
		categories_qs.update(team=to_team)

	# ------------------------------------------------------------------ #
	# 4. Lists (newsletter lists)                                          #
	# ------------------------------------------------------------------ #
	from subscriptions.models import Lists
	lists_qs = Lists.objects.filter(team=from_team)
	report.lists_moved = lists_qs.count()
	if not dry_run:
		lists_qs.update(team=to_team)

	# ------------------------------------------------------------------ #
	# 5. PredictionRunLog                                                  #
	# ------------------------------------------------------------------ #
	logs_qs = PredictionRunLog.objects.filter(team=from_team)
	report.prediction_logs_moved = logs_qs.count()
	if not dry_run:
		logs_qs.update(team=to_team)

	# ------------------------------------------------------------------ #
	# 6. Articles M2M                                                      #
	# ------------------------------------------------------------------ #
	from gregory.models import Articles
	articles = Articles.objects.filter(teams=from_team)
	report.articles_relinked = articles.count()
	if not dry_run:
		for article in articles:
			if not article.teams.filter(pk=to_team.pk).exists():
				article.teams.add(to_team)
			article.teams.remove(from_team)

	# ------------------------------------------------------------------ #
	# 7. Trials M2M                                                        #
	# ------------------------------------------------------------------ #
	from gregory.models import Trials
	trials = Trials.objects.filter(teams=from_team)
	report.trials_relinked = trials.count()
	if not dry_run:
		for trial in trials:
			if not trial.teams.filter(pk=to_team.pk).exists():
				trial.teams.add(to_team)
			trial.teams.remove(from_team)

	# ------------------------------------------------------------------ #
	# 8. Move model files on disk                                          #
	# ------------------------------------------------------------------ #
	_move_model_dir(from_team.slug, to_team.slug, report, dry_run=dry_run)

	return report
