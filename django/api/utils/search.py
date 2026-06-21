from django.db.models import Q

MAX_TERMS = 16
MAX_DEPTH = 8


def _term_q(text):
	"""Single term/phrase → OR across the GIN-indexed uppercase columns."""
	upper = text.upper()
	return Q(utitle__contains=upper) | Q(usummary__contains=upper)


def _tokenize(s):
	toks, i, n = [], 0, len(s)
	while i < n:
		c = s[i]
		if c.isspace():
			i += 1
			continue
		if c == '"':
			j = s.find('"', i + 1)
			if j == -1:
				toks.append(("PHRASE", s[i + 1:].strip()))
				break
			toks.append(("PHRASE", s[i + 1:j]))
			i = j + 1
			continue
		if c == '(':
			toks.append(("LP", "("))
			i += 1
			continue
		if c == ')':
			toks.append(("RP", ")"))
			i += 1
			continue
		if c == '-':
			toks.append(("NOT", "-"))
			i += 1
			continue
		j = i
		while j < n and not s[j].isspace() and s[j] not in '()"':
			j += 1
		w = s[i:j]
		i = j
		if w == "OR":
			toks.append(("OR", w))
		elif w == "AND":
			toks.append(("AND", w))
		elif w == "NOT":
			toks.append(("NOT", w))
		else:
			toks.append(("WORD", w))
	return toks


class _Parser:
	def __init__(self, toks):
		self.toks = toks
		self.pos = 0
		self.depth = 0
		self.terms = 0

	def _peek(self):
		return self.toks[self.pos] if self.pos < len(self.toks) else (None, None)

	def _next(self):
		t = self._peek()
		self.pos += 1
		return t

	def parse(self):
		return self._or()

	def _or(self):
		q = self._and()
		while self._peek()[0] == "OR":
			self._next()
			rhs = self._and()
			if rhs is not None:
				q = rhs if q is None else (q | rhs)
		return q

	def _and(self):
		q = None
		while True:
			t = self._peek()[0]
			if t in (None, "OR", "RP"):
				break
			if t == "AND":
				self._next()
				continue
			atom = self._unary()
			if atom is not None:
				q = atom if q is None else (q & atom)
		return q

	def _unary(self):
		if self._peek()[0] == "NOT":
			self._next()
			atom = self._atom()
			return ~atom if atom is not None else None
		return self._atom()

	def _atom(self):
		t, v = self._next()
		if t == "LP":
			self.depth += 1
			inner = self._or() if self.depth <= MAX_DEPTH else None
			if self._peek()[0] == "RP":
				self._next()
			self.depth -= 1
			return inner
		if t in ("WORD", "PHRASE"):
			v = (v or "").strip()
			if not v:
				return None
			self.terms += 1
			if self.terms > MAX_TERMS:
				return None
			return _term_q(v)
		return None


def build_search_q(raw):
	"""Parse a boolean search string into a Django Q object.

	Supports: AND (implicit between bare terms), OR (uppercase keyword),
	NOT/- (negation prefix), "quoted phrases" (contiguous match), and
	(parentheses) for grouping.

	Uses utitle/usummary GIN-indexed columns; each term is uppercased to
	match. Falls back to whole-string phrase match on any parse error so
	malformed input never raises a 500.

	Returns None for blank input (caller should skip filtering).
	"""
	raw = (raw or "").strip()
	if not raw:
		return None
	try:
		q = _Parser(_tokenize(raw)).parse()
		if q is None:
			raise ValueError("empty parse")
		return q
	except Exception:
		return _term_q(raw)
