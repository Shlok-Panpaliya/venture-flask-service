"""
Shared cookie fetch + Postgres persistence (same behavior as GET /storeCookies).
"""
import json

import requests

from config import DatabaseConfig
from postgres.client import get_db_cursor


def _fetch_cookies_via_requests():
    """Fetch cookies via HTTP only (works on Vercel/serverless; no Chrome needed)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    try:
        resp = session.get("https://mahabhunakasha.mahabhumi.gov.in/", timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch site: {e}") from e
    jsession_cookie_value = session.cookies.get("JSESSIONID")
    geNPRu9S_cookie_value = session.cookies.get("bnxpx9vG")
    return jsession_cookie_value, geNPRu9S_cookie_value


def refresh_cookies_in_database():
    """
    Fetch fresh cookies, replace the cookies row in Postgres, and return the dict
    returned in the JSON `data` field from /storeCookies.
    """
    try:
        jsession_cookie_value, geNPRu9S_cookie_value = _fetch_cookies_via_requests()
    except RuntimeError:
        raise

    if not jsession_cookie_value and not geNPRu9S_cookie_value:
        raise RuntimeError(
            "No JSESSIONID or bnxpx9vG cookies received. The site may set them via JavaScript; "
            "run /storeCookies on a host with Chrome or use a remote browser service."
        )

    conn, cur = get_db_cursor(DatabaseConfig.get_config())
    cur.execute("DELETE FROM cookies")
    data = {
        "jsession": jsession_cookie_value,
        "bnxpx9vG": geNPRu9S_cookie_value,
    }
    cur.execute("INSERT INTO cookies (data) VALUES (%s)", (json.dumps(data),))
    conn.commit()
    return data
