
import requests
import json

BASE_URL = "https://api.unpaywall.org"
VERSION = "/v2/"
EMAIL_QUERY_ARG = "email"

def getDataByDOI(doi: str, client_email: str):

    if doi and client_email:
        url = BASE_URL + VERSION + doi + "?" + EMAIL_QUERY_ARG + "=" + client_email
        headers = {
            'Accept': 'application/json'
        }
        response = requests.request("GET", url, headers=headers, data={})
        
        # Add error handling for empty responses
        if not response.text.strip():
            print(f"Empty response received for DOI: {doi}")
            return {}
            
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            print(f"Error decoding JSON from response for DOI: {doi}. Response: {response.text[:250]}")
            return {}
    else:
        raise Exception(f"DOI and Client Email cannot be empty! {doi}, {client_email}")

def checkIfDOIIsOpenAccess(doi: str, client_email: str):
    data = getDataByDOI(doi, client_email)
    if data and "is_oa" in data:
        return data["is_oa"]
    return False


def getOpenAccessURLForDOI(doi: str, client_email: str):
    data = getDataByDOI(doi, client_email)
    if data and "best_oa_location" in data:
        if "url" in data["best_oa_location"]:
            return data["best_oa_location"]["url"]
    return None