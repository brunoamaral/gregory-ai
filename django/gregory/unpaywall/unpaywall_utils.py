import logging
import os
import warnings

from unpywall import Unpywall
from unpywall.cache import UnpywallCache


class _EphemeralUnpywallCache(UnpywallCache):
	"""In-memory-only stand-in for unpywall's default cache.

	unpywall's built-in UnpywallCache persists every response to a single
	pickle file (named ``unpaywall_cache`` in the process's CWD) and
	rewrites that *entire* file on every new DOI lookup. Across a long
	backfill run that file grows without bound (rewrite cost grows with
	it), and it is shared — unguarded by any lock — with every other
	process that imports this module (e.g. the ingestion pipeline), so two
	processes writing at once can truncate/corrupt it. A corrupted file
	raises inside UnpywallCache.__init__ on the *next* lookup; because
	Unpywall.cache is only assigned after __init__ succeeds, that failure
	repeats for every subsequent DOI in the run, silently turning the rest
	of the run into a no-op. We don't need cross-process persistence here:
	callers already track processed article_ids themselves, so this cache
	only needs to live for the duration of one process and never touch disk.
	"""

	def __init__(self):
		self.content = {}
		self.access_times = {}
		self.timeout = None

	def save(self, name=None):
		return None


def _ensure_ephemeral_cache():
	if not isinstance(Unpywall.cache, _EphemeralUnpywallCache):
		Unpywall.init_cache(cache=_EphemeralUnpywallCache())


def getDataByDOI(doi: str, client_email: str):
	"""Return the full Unpaywall record for a DOI, or {} if not found / on error."""

	if not doi or not client_email:
		raise ValueError(
			f"DOI and client_email cannot be empty: {doi!r}, {client_email!r}"
		)

	_ensure_ephemeral_cache()

	os.environ["UNPAYWALL_EMAIL"] = client_email
	try:
		with warnings.catch_warnings():
			warnings.simplefilter("ignore", UserWarning)
			data = Unpywall.get_json(doi, errors="ignore")
		return data if data is not None else {}
	except Exception:
		logging.exception(f"Unpaywall error for DOI {doi}")
		return {}


def checkIfDOIIsOpenAccess(doi: str, client_email: str) -> bool:
	data = getDataByDOI(doi, client_email)
	return bool(data.get("is_oa")) if data else False


def getOpenAccessURLForDOI(doi: str, client_email: str):
	data = getDataByDOI(doi, client_email)
	if data and data.get("best_oa_location"):
		return data["best_oa_location"].get("url")
	return None
