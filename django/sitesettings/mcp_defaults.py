"""The three built-in MCP prompts, as plain data.

Adapted from mcp-server/gregory_mcp/prompts.py for storage as editable
SiteMcpPrompt rows (MCP-MULTI-TENANCY-PHASE-2-PLAN.md, decision 2 and PR B):

- research_topic drops its optional subject_id argument and tells the model
  to use list_subjects instead, since string.Template has no notion of an
  optional argument.
- recent_trials_for_subject asks the model for "six months before today"
  instead of computing the date, for the same reason.
- Neither prompt names the platform ("using the GregoryAI tools") per parent
  plan decision F: the MCP surface is single-tenant from the outside, so a
  user connected to one tenant's server should see that tenant and nothing
  else -- no platform name, no "instance"/"tenant" vocabulary.

This module imports no models, so it is safe to import from a migration.
Editing this list later does not retroactively change rows the migration
already created -- see the migration's module docstring.
"""

DEFAULT_MCP_PROMPTS = [
	{
		"name": "research_topic",
		"title": "Research a topic",
		"description": "Survey recent articles and clinical trials on a topic.",
		"ordering": 10,
		"arguments": [
			{"name": "topic", "description": "The topic to research.", "required": True},
		],
		"template": (
			'Research the topic "$topic".\n'
			"\n"
			'1. Call search_articles with search="$topic" (add relevant=true if you want '
			"AI-flagged-relevant results only) and skim the top results. To narrow to one "
			"research subject, call list_subjects first and pass its subject_id.\n"
			'2. Call search_trials with search="$topic" to find related clinical trials.\n'
			"3. For the most promising 2-3 articles or trials, call get_article / get_trial "
			"for the full record before summarizing.\n"
			"4. Summarize: what's being studied, how far along it is (trial "
			"phase/recruitment status where relevant), and any notable authors or "
			"sponsors."
		),
	},
	{
		"name": "recent_trials_for_subject",
		"title": "Recent trials for a subject",
		"description": (
			"List actively recruiting or recently registered trials for a research "
			"subject."
		),
		"ordering": 20,
		"arguments": [
			{
				"name": "subject_id",
				"description": "ID of the research subject (see list_subjects).",
				"required": True,
			},
		],
		"template": (
			"Find recent clinical trials for subject_id=$subject_id.\n"
			"\n"
			"1. Call search_trials with subject_id=$subject_id, "
			'recruitment_status_normalized="recruiting" and date_registration_after set '
			"to the date six months before today (YYYY-MM-DD), to bound it to recently "
			"registered trials. Results already come back newest-discovered-first by "
			"default.\n"
			"2. For each result, note phase, sponsor, and countries from the compact "
			"result — call get_trial only for ones worth a closer look.\n"
			"3. Summarize what's actively recruiting and where."
		),
	},
	{
		"name": "author_profile",
		"title": "Author profile",
		"description": (
			"Build a profile of a researcher: affiliation, publication history, "
			"relevance."
		),
		"ordering": 30,
		"arguments": [
			{
				"name": "name_or_orcid",
				"description": "The researcher's name or ORCID iD.",
				"required": True,
			},
		],
		"template": (
			'Build a profile of the researcher "$name_or_orcid".\n'
			"\n"
			"1. Call search_authors with search set to the given name or orcid to find "
			"the author_id (if it looks like an ORCID iD, pass it as orcid instead).\n"
			"2. Call get_author with that author_id (include_coauthors=true if "
			"collaboration network is relevant) for the full record.\n"
			"3. Optionally call search_articles with author_id=<id> to see their recent "
			"work.\n"
			"4. Summarize affiliation, publication volume, and how much of their work is "
			"AI-flagged relevant (relevant_articles_count vs articles_count)."
		),
	},
]
