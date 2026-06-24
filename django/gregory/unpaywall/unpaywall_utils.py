import logging
import os

from unpywall import Unpywall


def getDataByDOI(doi: str, client_email: str):
	"""Return the full Unpaywall record for a DOI, or {} if not found / on error."""

	if not doi or not client_email:
		raise ValueError(
			f"DOI and client_email cannot be empty: {doi!r}, {client_email!r}"
		)

	os.environ["UNPAYWALL_EMAIL"] = client_email
	try:
		data = Unpywall.get_json(doi, errors="ignore")
		return data if data is not None else {}
	except Exception as e:
		logging.error(f"Unpaywall error for DOI {doi}: {e}")
		return {}


def checkIfDOIIsOpenAccess(doi: str, client_email: str) -> bool:
	data = getDataByDOI(doi, client_email)
	return bool(data.get("is_oa")) if data else False


def getOpenAccessURLForDOI(doi: str, client_email: str):
	data = getDataByDOI(doi, client_email)
	if data and data.get("best_oa_location"):
		return data["best_oa_location"].get("url")
	return None
