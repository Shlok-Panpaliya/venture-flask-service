"""
API Client helper functions for external API calls
"""
import requests
import json


def getPlotInfo(surveyNumber, village_id, cookieResponse):
    """Fetch plot info from external API"""
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getPlotInfo"

    payload = f"state=27&giscode=RVM0709{village_id}&plotno={surveyNumber}&srs=4326"
    
    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Cookie': f'bnxpx9vG={cookieResponse["bnxpx9vG"]}; JSESSIONID={cookieResponse["jsession"]}',
    }
    
    response = requests.post(url, headers=headers, data=payload)

    return json.loads(response.text)


def getGeoInfo(plotId, village_id, cookieResponse):
    """Fetch geo info from external API"""
    url = "https://mahabhunakasha.mahabhumi.gov.in/rest/MapInfo/getExtentGeoref"
    
    payload = f"state=27&giscode=RVM0709{village_id}&plotid={plotId}&srs=4326"
    
    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Cookie': f'bnxpx9vG={cookieResponse["bnxpx9vG"]}; JSESSIONID={cookieResponse["jsession"]}',
    }
    
    response = requests.post(url, headers=headers, data=payload)
    return json.loads(response.text)
