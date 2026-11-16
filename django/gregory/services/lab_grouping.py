"""The rules that group authors into labs. Pure: no models and no queries.

``compute_labs`` takes plain tuples and dicts and returns a ``LabsResult``. The database
side (reading the inputs, writing the result) is in ``gregory.services.labs``.

A lab is a head (a senior author) plus the people who repeatedly publish with them. Head
status comes from the last-author position on papers that carry a lab signal.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

MIN_AUTHORS = 2  # a paper needs at least this many linked authors to carry a lab signal
MAX_AUTHORS = 30  # above this it is a consortium paper and carries none
MIN_LAST = 3  # last-author papers that make a person a head
MIN_SHARED = 2  # signal papers a person shares with a head to be a member of its lab
MIN_ARTICLES = 2  # articles an author needs in total to be placed at all
RECENCY_YEARS = 10  # papers newer than (current year - this) get a bonus per year
RECENCY_BONUS_TENTHS = 3  # 0.3 per year, as integer tenths so that sums compare exactly
BASE_WEIGHT_TENTHS = 10  # every shared paper weighs 1.0 before the recency bonus

ROLE_HEAD = "head"
ROLE_MEMBER = "member"
ROLE_COLLABORATOR = "collaborator"


@dataclass
class Assignment:
	"""Where one author row lands. Every row of a person gets the same lab and role."""

	author_id: int
	head_id: int  # the lab, keyed by its head's author_id
	role: str
	same_institution: bool | None  # None when no shared paper has institutions on both sides
	shared_articles: int  # signal papers shared with the head; a head's own last-author papers
	last_shared_year: int | None


@dataclass
class LabResult:
	head_id: int
	head_rows: list  # every author row of the head's person, the head row included
	institution_id: int | None
	last_author_articles: int
	member_count: int = 0  # member rows, not counting the head's rows
	core_count: int = 0  # of those, the ones that share an institution with the head
	collaborator_count: int = 0


@dataclass
class LabsResult:
	labs: dict = field(default_factory=dict)  # head author_id -> LabResult
	assignments: dict = field(default_factory=dict)  # author_id -> Assignment


def compute_labs(
	rows, *, authors, article_years, article_author_counts, manual_heads, existing_heads, today
):
	"""Group authors into labs.

	``rows``: ``(article_id, author_id, position, institution_ids)`` per authorship.
	``authors``: ``{author_id: (openalex_id or None, orcid or None, article_count)}``.
	``article_years``: ``{article_id: published year}``; a missing year gets no recency bonus.
	``article_author_counts``: ``{article_id: number of linked authors}``.
	``manual_heads``: author ids heading a manual lab, always heads. ``existing_heads``:
	author ids heading any lab now, so a person's head row stays stable between runs.
	``today``: a date; the recency window is relative to its year.
	"""
	base_year = today.year - RECENCY_YEARS

	def weight(article):
		year = article_years.get(article)
		if not year:
			return BASE_WEIGHT_TENTHS
		return BASE_WEIGHT_TENTHS + RECENCY_BONUS_TENTHS * max(0, year - base_year)

	def person(author):  # authors sharing an OpenAlex ID are one person
		info = authors.get(author)
		return (info[0] if info else None) or author

	def orcid(author):
		info = authors.get(author)
		return info[1] if info else None

	# Rule 1: person grouping.
	person_rows = defaultdict(set)
	for author in authors:
		person_rows[person(author)].add(author)
	for author in manual_heads:
		person_rows[person(author)].add(author)

	# Rows per (person, article), the articles of each person, and each paper's last author.
	ship = {}
	person_articles = defaultdict(list)
	last_row = {}
	for article, author, position, institutions in rows:
		p = person(author)
		person_rows[p].add(author)
		key = (p, article)
		institutions = frozenset(institutions)
		if key in ship:
			ship[key] |= institutions
		else:
			ship[key] = institutions
			person_articles[p].append(article)
		# Rule 2: signal papers.
		if position == "last" and MIN_AUTHORS <= article_author_counts.get(article, 0) <= MAX_AUTHORS:
			if article not in last_row or author < last_row[article]:
				last_row[article] = author
	last_person = {article: person(author) for article, author in last_row.items()}

	last_papers = defaultdict(set)
	for article, p in last_person.items():
		last_papers[p].add(article)

	# Rule 3: heads.
	head_people = {p for p, papers in last_papers.items() if len(papers) >= MIN_LAST}
	head_people |= {person(a) for a in manual_heads}

	# Rule 4: the head's row, stable across runs.
	head_row = {}
	for p in head_people:
		candidates = person_rows[p]
		fixed = sorted(a for a in candidates if a in manual_heads) or sorted(
			a for a in candidates if a in existing_heads
		)
		if fixed:
			head_row[p] = fixed[0]
			continue
		per_row = Counter(last_row[article] for article in last_papers.get(p, ()))
		head_row[p] = min(candidates, key=lambda a: (-per_row[a], 0 if orcid(a) else 1, a))

	result = LabsResult()

	def last_year_of(articles):
		years = [article_years[a] for a in articles if article_years.get(a)]
		return max(years) if years else None

	# Rules 10 and 11: the head's own rows, and the lab's institution.
	for p in head_people:
		head = head_row[p]
		papers = last_papers.get(p, set())
		weights = Counter()
		for article in person_articles.get(p, ()):
			for institution in ship[(p, article)]:
				weights[institution] += weight(article)
		institution_id = max(weights, key=lambda i: (weights[i], -i)) if weights else None
		result.labs[head] = LabResult(
			head_id=head,
			head_rows=sorted(person_rows[p]),
			institution_id=institution_id,
			last_author_articles=len(papers),
		)
		year = last_year_of(papers)
		for row in person_rows[p]:
			result.assignments[row] = Assignment(row, head, ROLE_HEAD, None, len(papers), year)

	# Rules 6 to 9: members and collaborators.
	for p, rows_of_p in person_rows.items():
		if p in head_people:
			continue
		if sum(authors[a][2] for a in rows_of_p if a in authors) < MIN_ARTICLES:
			continue
		shared = defaultdict(list)  # head person -> shared signal papers
		for article in person_articles.get(p, ()):
			h = last_person.get(article)
			if h is not None and h != p and h in head_people:
				shared[h].append(article)
		if not shared:
			continue

		def rank(h):  # rule 8: score, then shared papers, then recency, then lowest head id
			papers = shared[h]
			return (
				sum(weight(a) for a in papers),
				len(papers),
				last_year_of(papers) or 0,
				-head_row[h],
			)

		strong = [h for h in shared if len(shared[h]) >= MIN_SHARED]
		if strong:
			best, role = max(strong, key=rank), ROLE_MEMBER
		else:
			best, role = max(shared, key=rank), ROLE_COLLABORATOR
		papers = shared[best]

		# Rule 9: core or external, per shared paper where both sides have institutions.
		known = same = 0
		for article in papers:
			theirs, head_side = ship[(p, article)], ship[(best, article)]
			if theirs and head_side:
				known += 1
				same += bool(theirs & head_side)
		same_institution = None if not known else same * 2 >= known

		lab = result.labs[head_row[best]]
		year = last_year_of(papers)
		for row in rows_of_p:
			result.assignments[row] = Assignment(
				row, lab.head_id, role, same_institution, len(papers), year
			)
			if role == ROLE_MEMBER:
				lab.member_count += 1
				lab.core_count += bool(same_institution)
			else:
				lab.collaborator_count += 1

	return result
