"""Starter prompts. Three to start; let real usage dictate the rest."""

from __future__ import annotations

from datetime import date, timedelta


def register_prompts(server) -> None:
	@server.prompt(
		name="research_topic",
		title="Research a topic",
		description="Survey recent articles and clinical trials on a topic.",
	)
	def research_topic(topic: str, subject_id: str | None = None) -> str:
		scope = f" within subject_id={subject_id}" if subject_id else ""
		return (
			f"Research the topic \"{topic}\"{scope} using the GregoryAI tools.\n\n"
			f"1. Call search_articles with search=\"{topic}\" (add relevant=true if you "
			"want AI-flagged-relevant results only) and skim the top results.\n"
			f"2. Call search_trials with search=\"{topic}\" to find related clinical trials.\n"
			"3. For the most promising 2-3 articles or trials, call get_article / get_trial "
			"for the full record before summarizing.\n"
			"4. Summarize: what's being studied, how far along it is (trial phase/recruitment "
			"status where relevant), and any notable authors or sponsors."
		)

	@server.prompt(
		name="recent_trials_for_subject",
		title="Recent trials for a subject",
		description="List actively recruiting or recently registered trials for a research subject.",
	)
	def recent_trials_for_subject(subject_id: str) -> str:
		# date_registration is not a valid `ordering` value on /trials/ (see
		# TrialViewSet.ordering_fields) — DRF's OrderingFilter silently ignores
		# an unrecognised value rather than rejecting it, so ordering by it
		# quietly fell back to the default order instead of erroring. Bound
		# recency with the date_registration_after filter instead; the default
		# ordering (-discovery_date) already puts newest-discovered first.
		cutoff = (date.today() - timedelta(days=180)).isoformat()
		return (
			f"Find recent clinical trials for subject_id={subject_id} using the GregoryAI tools.\n\n"
			f"1. Call search_trials with subject_id={subject_id}, "
			f'recruitment_status_normalized="recruiting", date_registration_after="{cutoff}" '
			"(roughly the last 6 months) to bound it to recently-registered trials — "
			"results already come back newest-discovered-first by default.\n"
			"2. For each result, note phase, sponsor, and countries from the compact result — "
			"call get_trial only for ones worth a closer look.\n"
			"3. Summarize what's actively recruiting and where."
		)

	@server.prompt(
		name="author_profile",
		title="Author profile",
		description="Build a profile of a researcher: affiliation, publication history, relevance.",
	)
	def author_profile(name_or_orcid: str) -> str:
		return (
			f'Build a profile of the researcher "{name_or_orcid}" using the GregoryAI tools.\n\n'
			"1. Call search_authors with search set to the given name or orcid to find the "
			"author_id (if it looks like an ORCID iD, pass it as orcid instead).\n"
			"2. Call get_author with that author_id (include_coauthors=true if collaboration "
			"network is relevant) for the full record.\n"
			"3. Optionally call search_articles with author_id=<id> to see their recent work.\n"
			"4. Summarize affiliation, publication volume, and how much of their work is "
			"AI-flagged relevant (relevant_articles_count vs articles_count)."
		)
