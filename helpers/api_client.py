"""
API Client helper functions for external API calls
"""
import json
import requests

from helpers.cookie_service import refresh_cookies_in_database


def getPlotInfo(surveyNumber, village_id, cookieResponse, _retry=True):
    """Fetch plot info from external API."""
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getPlotInfo"

    payload = f"state=27&giscode=RVM0709{village_id}&plotno={surveyNumber}&srs=4326"

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": f'bnxpx9vG={cookieResponse["bnxpx9vG"]}; JSESSIONID={cookieResponse["jsession"]}',
    }
    print(f"Headers: {headers}")
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        response.raise_for_status()
        return json.loads(response.text), cookieResponse
    except (requests.RequestException, json.JSONDecodeError):
        if not _retry:
            raise
        new_cookies = refresh_cookies_in_database()
        return getPlotInfo(surveyNumber, village_id, new_cookies, _retry=False)


def getGeoInfo(plotId, village_id, cookieResponse, _retry=True):
    """Fetch geo info from external API."""
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getExtentGeoref"

    payload = f"state=27&giscode=RVM0709{village_id}&plotid={plotId}&srs=4326"

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": f'bnxpx9vG={cookieResponse["bnxpx9vG"]}; JSESSIONID={cookieResponse["jsession"]}',
    }

    try:
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        response.raise_for_status()
        return json.loads(response.text), cookieResponse
    except (requests.RequestException, json.JSONDecodeError):
        if not _retry:
            raise
        new_cookies = refresh_cookies_in_database()
        return getGeoInfo(plotId, village_id, new_cookies, _retry=False)
