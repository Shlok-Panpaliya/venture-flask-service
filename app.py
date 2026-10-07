from flask import Flask, json, request, jsonify
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException
from postgres.client import get_db_cursor
from config import DatabaseConfig
from flask_cors import CORS
import ast  # for literal_eval
import time  # for sleep functionality
import unicodedata
from PyPDF2 import PdfReader, PdfWriter
import io
import base64
import requests
from flask import request, jsonify
import re
import json
import random
from helpers.api_client import getPlotInfo, getGeoInfo
from helpers.cookie_service import refresh_cookies_in_database
from helpers.data_parser import getPlotInfoFromString
from helpers.bhulekh_captcha import (
    captcha_png_bytes_from_data_src,
    ocr_bhulekh_captcha_for_driver,
    ocr_bhulekh_captcha_png,
)
from flask_caching import Cache

app = Flask(__name__)
CORS(app)



# Simple in-memory cache (you can replace with Redis for production)
cache = Cache(app, config={
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 24 * 60 * 60  # 24 hours in seconds
})

@app.route('/test-db')
def test_database_connection():
    """Test database connection and return status"""
    try:
        success, message = DatabaseConfig.test_connection()
        config = DatabaseConfig.get_config()
        
        # Don't expose password in response
        safe_config = {k: v if k != 'password' else '***' for k, v in config.items()}
        
        return jsonify({
            "success": success,
            "message": message,
            "config": safe_config
        }), 200 if success else 500
        
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"Connection test failed: {str(e)}"
        }), 500


@app.route('/storeCookies')
def get_cookie():
    try:
        data = refresh_cookies_in_database()
    except RuntimeError as e:
        return jsonify({"message": str(e), "code": 500}), 500

    return jsonify({"message": "Cookies stored successfully", "code": 200, "data": data})


@app.route('/getCookies')
def get_cookies():
    try:
        # Connect to postgres supabase and get the cookies from the database
        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        
        # Fetch cookies data
        cur.execute("SELECT data FROM cookies ORDER BY id DESC LIMIT 1")
        cookie_data = cur.fetchone()
        
        if not cookie_data:
            return jsonify({"error": "No cookies found in database"}), 404

        cookies = cookie_data[0]
        
        return jsonify({
            "success": True,
            "cookies": cookies,
            "message": "Cookies retrieved successfully"
        })
        
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


def _find_relation(cur, candidates):
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        ORDER BY array_position(%s, table_name)
        LIMIT 1
    """, (candidates, candidates))
    row = cur.fetchone()
    return row[0] if row else None


def _find_column(cur, table_name, candidates):
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = %s
          AND column_name = ANY(%s)
        ORDER BY array_position(%s, column_name)
        LIMIT 1
    """, (table_name, candidates, candidates))
    row = cur.fetchone()
    return row[0] if row else None


def _location_value(row, column_names, preferred):
    if not row:
        return None
    value = row.get(preferred)
    if value is not None:
        return value
    for name in column_names:
        if row.get(name) is not None:
            return row[name]
    return None


@app.route('/getDistricts')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_districts():
    """Return districts for the Land Intelligence location selector."""
    try:
        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        table = _find_relation(cur, ['district', 'districts'])
        if table:
            id_column = _find_column(cur, table, ['id', 'district_id'])
            name_column = _find_column(cur, table, ['name', 'district_name'])
            english_column = _find_column(cur, table, ['english_name', 'englishname', 'english_name_en'])
            if id_column and name_column:
                columns = [id_column, name_column] + ([english_column] if english_column and english_column not in [id_column, name_column] else [])
                select_sql = ', '.join('"' + col.replace('"', '""') + '"' for col in columns)
                table_sql = '"' + table.replace('"', '""') + '"'
                cur.execute(f'SELECT {select_sql} FROM {table_sql}')
                return jsonify([
                    {
                        "id": row[0],
                        "name": row[1],
                        "englishName": row[2] if english_column else row[1],
                    }
                    for row in cur.fetchall()
                ])

        # Fallback for databases that do not contain a separate district table:
        # derive district identifiers from the taluka/village hierarchy.
        taluka_table = _find_relation(cur, ['taluka', 'talukas'])
        if taluka_table:
            district_column = _find_column(cur, taluka_table, ['district_id', 'districtId'])
            if district_column:
                dc = '"' + district_column.replace('"', '""') + '"'
                tc = '"' + taluka_table.replace('"', '""') + '"'
                cur.execute(f'SELECT DISTINCT {dc} FROM {tc} WHERE {dc} IS NOT NULL ORDER BY {dc}')
                return jsonify([
                    {
                        "id": row[0],
                        "name": "Amravati" if str(row[0]) == "7" else str(row[0]),
                        "englishName": "Amravati" if str(row[0]) == "7" else str(row[0]),
                    }
                    for row in cur.fetchall()
                ])

        village_table = _find_relation(cur, ['village', 'villages'])
        if village_table:
            district_column = _find_column(cur, village_table, ['district_id', 'districtId'])
            if district_column:
                dc = '"' + district_column.replace('"', '""') + '"'
                vc = '"' + village_table.replace('"', '""') + '"'
                cur.execute(f'SELECT DISTINCT {dc} FROM {vc} WHERE {dc} IS NOT NULL ORDER BY {dc}')
                return jsonify([
                    {
                        "id": row[0],
                        "name": "Amravati" if str(row[0]) == "7" else str(row[0]),
                        "englishName": "Amravati" if str(row[0]) == "7" else str(row[0]),
                    }
                    for row in cur.fetchall()
                ])

        return jsonify([]), 200
    except Exception as e:
        print(f"Error in get_districts: {str(e)}")
        return jsonify({"error": "Unable to load districts"}), 500


@app.route('/getTalukas')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_talukas():
    """Return talukas belonging to a district."""
    district_id = request.args.get('district_id')
    if not district_id:
        return jsonify({"error": "district_id is required"}), 400

    try:
        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        table = _find_relation(cur, ['taluka', 'talukas'])

        if table:
            id_column = _find_column(cur, table, ['id', 'taluka_id'])
            name_column = _find_column(cur, table, ['name', 'taluka_name'])
            english_column = _find_column(cur, table, ['english_name', 'englishname', 'english_name_en'])
            district_column = _find_column(cur, table, ['district_id', 'districtId'])

            if id_column and name_column and district_column:
                columns = [id_column, name_column]
                if english_column and english_column not in columns:
                    columns.append(english_column)

                select_sql = ', '.join('"' + col.replace('"', '""') + '"' for col in columns)
                table_sql = '"' + table.replace('"', '""') + '"'
                where_column = '"' + district_column.replace('"', '""') + '"'
                order_column = '"' + (english_column or name_column).replace('"', '""') + '"'

                try:
                    cur.execute(
                        f'SELECT {select_sql} FROM {table_sql} '
                        f'WHERE {where_column} = %s ORDER BY {order_column}',
                        (district_id,),
                    )
                except Exception:
                    conn.rollback()
                else:
                    return jsonify([
                        {
                            "id": row[0],
                            "name": row[1],
                            "englishName": row[2] if english_column else row[1],
                            "districtId": district_id,
                        }
                        for row in cur.fetchall()
                    ])

        # Fallback: derive taluka identifiers from the existing village table.
        village_table = _find_relation(cur, ['village', 'villages'])
        if village_table:
            taluka_column = _find_column(cur, village_table, ['taluka_id', 'talukaId'])
            if taluka_column:
                tc = '"' + taluka_column.replace('"', '""') + '"'
                vc = '"' + village_table.replace('"', '""') + '"'
                cur.execute(
                    f'SELECT DISTINCT {tc} FROM {vc} '
                    f'WHERE {tc} IS NOT NULL ORDER BY {tc}'
                )
                return jsonify([
                    {
                        "id": row[0],
                        "name": str(row[0]),
                        "englishName": str(row[0]),
                        "districtId": district_id,
                    }
                    for row in cur.fetchall()
                ])

        return jsonify([])
    except Exception as e:
        print(f"Error in get_talukas: {str(e)}")
        return jsonify({"error": "Unable to load talukas"}), 500


@app.route('/getVillages')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_villages():
    taluka_id = request.args.get('taluka_id')  # Get taluka_id from query string
    # connect to postgres supabase and get the villages from the database
    conn, cur = get_db_cursor(DatabaseConfig.get_config())
    
    cur.execute("""
        SELECT v.*, COUNT(s.id) as survey_count 
        FROM village v 
        LEFT JOIN surveynumber s ON v.id = s.village_id 
        WHERE v.taluka_id = %s 
        GROUP BY v.id
    """, (taluka_id,))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    villages = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        villages.append({
            "id": row_dict.get("id"),
            "name": row_dict.get("name"),
            "englishName": row_dict.get("english_name"),
            "talukaId": row_dict.get("taluka_id"),
            "surveyCount": row_dict.get("survey_count")
        })
    return jsonify(villages)

@app.route('/getSurveyNumbers')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_survey_numbers():
    village_id = request.args.get('village_id')
    # connect to postgres supabase and get the survey numbers from the database
    conn, cur = get_db_cursor(DatabaseConfig.get_config())
    
    # Corrected query using a subquery to handle the aggregation
    cur.execute("""
        SELECT COALESCE(json_agg(json_build_object(
            'id', subq.id,
            'number', subq.number,
            'villageId', subq.village_id,
            'plotCount', subq.plot_count
        )), '[]'::json) as result
        FROM (
            SELECT 
                s.id, 
                s.number, 
                s.village_id,
                COUNT(p.id)::integer as plot_count
            FROM surveynumber s 
            LEFT JOIN property p ON s.id = p.survey_number_id 
            WHERE s.village_id = %s 
            GROUP BY s.id, s.number, s.village_id
            ORDER BY 
                -- First sort by the numeric part before any slash or letter
                CASE 
                    WHEN s.number ~ '^[0-9]+$' THEN s.number::INTEGER
                    WHEN s.number ~ '^[0-9]+/' THEN SPLIT_PART(s.number, '/', 1)::INTEGER
                    WHEN s.number ~ '^[0-9]+[A-Z]' THEN REGEXP_REPLACE(s.number, '[^0-9].*', '')::INTEGER
                    ELSE 999999
                END ASC,
                -- Then sort by the part after slash/letter
                CASE 
                    WHEN s.number ~ '^[0-9]+/[0-9]+$' THEN SPLIT_PART(s.number, '/', 2)::INTEGER
                    WHEN s.number ~ '^[0-9]+/[A-Z]$' THEN ASCII(SPLIT_PART(s.number, '/', 2))
                    WHEN s.number ~ '^[0-9]+[A-Z]$' THEN ASCII(REGEXP_REPLACE(s.number, '^[0-9]+', ''))
                    ELSE 0
                END ASC
        ) subq
    """, (village_id,))
    
    result = cur.fetchone()
    return jsonify(result[0] if result[0] is not None else [])

@app.route('/getProperties')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_properties():
    survey_number_id = request.args.get('survey_number_id')
    conn, cur = get_db_cursor(DatabaseConfig.get_config())

    cur.execute("SELECT * FROM property WHERE survey_number_id = %s", (survey_number_id,))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    properties = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        properties.append(row_dict)
    return jsonify(properties)

@app.route('/getProperty')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_property():
    property_id = request.args.get('property_id')
    survey_number_id = request.args.get('survey_number_id')
    village_id = request.args.get('village_id')
    
    conn, cur = get_db_cursor(DatabaseConfig.get_config())

    cur.execute("""
        SELECT p.* 
        FROM property p
        JOIN surveynumber s ON p.survey_number_id = s.id
        WHERE p.id = %s 
        AND p.survey_number_id = %s 
        AND s.village_id = %s
    """, (property_id, survey_number_id, village_id))
    
    columns = [desc[0] for desc in cur.description]
    row = cur.fetchone()
    
    if row is None:
        return jsonify({"error": "Property not found"}), 404
        
    property_data = dict(zip(columns, row))
    return jsonify(property_data)

@app.route('/getPlotReportPDF')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_plot_report_pdf():
    try:
        giscode = request.args.get('giscode')
        plotno = request.args.get('plotno')
        
        if not giscode or not plotno:
            return jsonify({"error": "giscode and plotno are required"}), 400

        # Get cookies from database
        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        
        # Fetch cookies data
        cur.execute("SELECT data FROM cookies")
        cookie_data = cur.fetchone()
        
        if not cookie_data:
            return jsonify({"error": "No cookies found in database"}), 404

        cookies = cookie_data[0]
        
        # Prepare the request
        url = 'https://mahabhunakasha.mahabhumi.gov.in/rest/Reports/PlotReportPDFBase64'
        headers = {
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': 'https://mahabhunakasha.mahabhumi.gov.in',
            'Referer': f'https://mahabhunakasha.mahabhumi.gov.in/signplotreportpublic.jsp?state=27&giscode={giscode}&plotno={plotno}',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
            'X-Requested-With': 'XMLHttpRequest',
            'sec-ch-ua': '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"'
        }
        
        cookies_dict = {
            'ext_name': 'ojplmecpdpgccookcobabopnaifgidhf',
            'bnxpx9vG': cookies['bnxpx9vG'],
            'JSESSIONID': cookies['jsession']
        }
        
        data = {
            'state': '27',
            'giscode': giscode,
            'plotno': plotno,
            'sameownerplotreport': 'false',
            'derivedlayerids': '-1,',
            'selectedlayerids': '-1,',
            'scaletextfield': '0',
            'dscstatus': ''
        }
        
        try:
            # Get the full PDF
            response = requests.post(url, headers=headers, cookies=cookies_dict, data=data)
            response.raise_for_status()
            
            # Decode the base64 PDF content
            pdf_bytes = base64.b64decode(response.text)
            
            # Extract first page using PyPDF2
            pdf_reader = PdfReader(io.BytesIO(pdf_bytes))
            if len(pdf_reader.pages) == 0:
                return jsonify({"error": "PDF has no pages"}), 400
                
            pdf_writer = PdfWriter()
            pdf_writer.add_page(pdf_reader.pages[0])
            
            # Convert back to base64
            output = io.BytesIO()
            pdf_writer.write(output)
            first_page_base64 = base64.b64encode(output.getvalue()).decode('utf-8')
            
            return jsonify({"pdf_base64": first_page_base64})
            
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Request failed: {str(e)}"}), 500
        except Exception as e:
            return jsonify({"error": f"PDF processing failed: {str(e)}"}), 500
            
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500

@app.route('/getTDRCertificates')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_tdr_certificates():
    # Sample data for TDR certificates
    sample_tdr_certificates = [
        {
            "id": 1,
            "tdrOwnerName": "Rajesh Kumar Patel",
            "folioNumber": "TDR-2024-001",
            "fsiCreditArea": "150.50 sq.m",
            "pdfLink": "https://drive.google.com/file/d/1-fJNBOKCG1y8KUrKd0rWNM1M7j-6MBif/view?usp=drive_link"
        },
        {
            "id": 2,
            "tdrOwnerName": "Sunita Devi Sharma",
            "folioNumber": "TDR-2024-002",
            "fsiCreditArea": "200.75 sq.m",
            "pdfLink": "https://drive.google.com/file/d/1-fJNBOKCG1y8KUrKd0rWNM1M7j-6MBif/view?usp=drive_link"
        },
        {
            "id": 3,
            "tdrOwnerName": "Amit Singh Thakur",
            "folioNumber": "TDR-2024-003",
            "fsiCreditArea": "125.25 sq.m",
            "pdfLink": "https://drive.google.com/file/d/1-fJNBOKCG1y8KUrKd0rWNM1M7j-6MBif/view?usp=drive_link"
        },
        {
            "id": 4,
            "tdrOwnerName": "Priya Gupta",
            "folioNumber": "TDR-2024-004",
            "fsiCreditArea": "180.00 sq.m",
            "pdfLink": "https://drive.google.com/file/d/1-fJNBOKCG1y8KUrKd0rWNM1M7j-6MBif/view?usp=drive_link"
        },
        {
            "id": 5,
            "tdrOwnerName": "Vikram Malhotra",
            "folioNumber": "TDR-2024-005",
            "fsiCreditArea": "95.30 sq.m",
            "pdfLink": "https://drive.google.com/file/d/1-fJNBOKCG1y8KUrKd0rWNM1M7j-6MBif/view?usp=drive_link"
        }
    ]
    
    return jsonify({
        "success": True,
        "data": sample_tdr_certificates,
        "count": len(sample_tdr_certificates)
    })

def _wait_aspnet_postback(driver, wait, seconds=2.5):
    """Bhulekh triggers __doPostBack on dropdown change; brief wait for partial/full postback."""
    time.sleep(seconds)
    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception:
        pass


def _scroll_into_view(driver, element, block="center"):
    driver.execute_script(
        "arguments[0].scrollIntoView({block: arguments[1], inline: 'nearest'});",
        element,
        block,
    )


def _safe_click(driver, element):
    """
    Bhulekh often shows footers, accessibility widgets, or modals over lower buttons;
    native click then fails with 'element click intercepted'. Scroll + JS click fallback.
    """
    _scroll_into_view(driver, element, "center")
    time.sleep(0.35)
    try:
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def _normalize_captcha(s):
    if not s:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", str(s).strip())[:6]


def _wait_captcha_data_url(driver, timeout=25):
    def ready(d):
        src = d.find_element(By.ID, "ContentPlaceHolder1_captchaImage").get_attribute("src") or ""
        return src.startswith("data:image") and "," in src and len(src) > 80

    WebDriverWait(driver, timeout).until(ready)


def _refresh_bhulekh_captcha(driver):
    img = driver.find_element(By.ID, "ContentPlaceHolder1_captchaImage")
    old_src = img.get_attribute("src") or ""
    ref_btn = driver.find_element(By.ID, "ContentPlaceHolder1_btnreferesh")
    _safe_click(driver, ref_btn)

    def src_changed(d):
        n = d.find_element(By.ID, "ContentPlaceHolder1_captchaImage").get_attribute("src") or ""
        return n != old_src and n.startswith("data:image")

    WebDriverWait(driver, 15).until(src_changed)
    time.sleep(0.3)


_zw_chars = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def _norm_bhulekh_label(s):
    if s is None:
        return ""
    t = _zw_chars.sub("", str(s)).strip()
    return unicodedata.normalize("NFC", t)


def _survey_part1_for_bhulekh(survey_raw):
    """
    Bhulekh सर्वे नंबर(भाग-1) expects the first numeric segment only, e.g. 113/3/1 -> 113.
    """
    if survey_raw is None:
        return ""
    s = str(survey_raw).strip()
    if not s:
        return ""
    first = s.split("/")[0].strip()
    return first[:10] if first else ""


def _random_mobile_94():
    """10-digit Indian-style mobile: 94 + 8 random digits."""
    return "94" + "".join(str(random.randint(0, 9)) for _ in range(8))


def _wait_ddlsurveyno_populated(driver, wait, timeout=30):
    """After शोधा, सर्वे नंबर dropdown must list real options (not only --निवडा--)."""

    def has_survey_options(d):
        el = d.find_element(By.ID, "ContentPlaceHolder1_ddlsurveyno")
        for opt in Select(el).options:
            v = opt.get_attribute("value") or ""
            if v and v != "--निवडा--":
                return True
        return False

    WebDriverWait(driver, timeout).until(has_survey_options)


def _select_ddlsurveyno_exact(select_el, survey_raw):
    """
    Pick ContentPlaceHolder1_ddlsurveyno option matching API survey_number (e.g. 113/3/1).
    Option value and visible text match; Unicode/ZW normalized if needed.
    """
    raw = str(survey_raw).strip() if survey_raw else ""
    if not raw:
        raise ValueError("survey_number is empty")
    target_norm = _norm_bhulekh_label(raw)
    sel = Select(select_el)
    for attempt in (raw, target_norm):
        try:
            sel.select_by_value(attempt)
            return attempt
        except Exception:
            continue
    for opt in sel.options:
        v = opt.get_attribute("value") or ""
        if not v or v == "--निवडा--":
            continue
        if _norm_bhulekh_label(v) == target_norm:
            sel.select_by_value(v)
            return v
    preview = [
        opt.get_attribute("value")
        for opt in sel.options[:40]
        if opt.get_attribute("value") not in (None, "", "--निवडा--")
    ]
    raise NoSuchElementException(
        f"सर्वे नंबर: no <option> matched {raw!r} (normalized={target_norm!r}). "
        f"Sample values: {preview!r}"
    )


def _select_bhulekh_dropdown(select_el, *, value=None, label=None, field_name="dropdown"):
    """
    select_by_visible_text often fails on Devanagari due to NFC/NFD or copy-paste variants.
    Prefer *_id + select_by_value; otherwise match option text after NFC normalize.
    """
    sel = Select(select_el)
    if value is not None and str(value).strip() != "":
        sel.select_by_value(str(value))
        return
    if not label or not str(label).strip():
        raise ValueError(f"{field_name}: pass value or non-empty label")
    target = _norm_bhulekh_label(label)
    try:
        sel.select_by_visible_text(label.strip())
        return
    except Exception:
        pass
    for opt in sel.options:
        t = opt.text
        if t and _norm_bhulekh_label(t) == target:
            sel.select_by_value(opt.get_attribute("value"))
            return
    substr_matches = [
        opt for opt in sel.options
        if opt.text
        and (
            target == _norm_bhulekh_label(opt.text)
            or target in _norm_bhulekh_label(opt.text)
            or _norm_bhulekh_label(opt.text) in target
        )
    ]
    if len(substr_matches) == 1:
        sel.select_by_value(substr_matches[0].get_attribute("value"))
        return
    preview = [opt.text for opt in sel.options[:20] if opt.text]
    raise NoSuchElementException(
        f"{field_name}: no option matched label {label!r} (normalized={target!r}). "
        f"Use {field_name}_id from the <option value=\"…\"> instead. Sample options: {preview!r}"
    )


@app.route('/get712PDF')
def get_7_12_pdf():
    """
    Fill 7/12 form on https://bhulekh.mahabhumi.gov.in/ through district → taluka → village
    through सर्वे नंबर dropdown, random 94xxxxxxxx mobile, OCR captcha (Tesseract), and Submit.
    Optional: ?captcha_manual=Ab12cd to skip OCR (max 6 alphanumeric).

    Sample query (values from live markup — Amravati / Akoli Part 1):
      /get712PDF?district_id=7&taluka_id=9&village_id=270700090077070000&survey_number=113/3/1
      (भाग-1 = 113, Search, then selects ddlsurveyno value matching 113/3/1 if present)

    Or by visible labels:
      /get712PDF?district=अमरावती&taluka=अमरावती&village=अकोली भाग 1&survey_number=113/3/1

    This route is not cached (Selenium must run on every request).
    For local debugging, append e.g. &debug_keep_browser_seconds=15 to keep the window
    open before it closes (HTTP response is delayed until the wait finishes).
    """
    try:
        district = request.args.get('district')
        district_id = request.args.get('district_id')
        taluka = request.args.get('taluka')
        taluka_id = request.args.get('taluka_id')
        village = request.args.get('village')
        village_id = request.args.get('village_id')
        survey_number = request.args.get('survey_number')
        captcha_manual = request.args.get('captcha_manual')
        debug_keep_browser_seconds = request.args.get('debug_keep_browser_seconds', type=int)

        if not survey_number:
            return jsonify({"error": "survey_number is required"}), 400
        if not district and not district_id:
            return jsonify({"error": "district or district_id is required"}), 400
        if not taluka and not taluka_id:
            return jsonify({"error": "taluka or taluka_id is required"}), 400
        if not village and not village_id:
            return jsonify({"error": "village or village_id is required"}), 400

        chrome_options = Options()
        # chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')

        driver = webdriver.Chrome(options=chrome_options)
        wait = WebDriverWait(driver, 30)

        try:
            driver.get('https://bhulekh.mahabhumi.gov.in/')
            wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_ddlMainDist')))

            # District — id ContentPlaceHolder1_ddlMainDist
            dist_el = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_ddlMainDist')))
            _scroll_into_view(driver, dist_el, "center")
            _select_bhulekh_dropdown(
                dist_el, value=district_id, label=district, field_name="district",
            )
            _wait_aspnet_postback(driver, wait)

            # Taluka — id ContentPlaceHolder1_ddlTalForAll
            tal_el = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_ddlTalForAll')))
            _scroll_into_view(driver, tal_el, "center")
            _select_bhulekh_dropdown(
                tal_el, value=taluka_id, label=taluka, field_name="taluka",
            )
            _wait_aspnet_postback(driver, wait)

            # Village — id ContentPlaceHolder1_ddlVillForAll (prefer village_id)
            vill_el = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_ddlVillForAll')))
            _scroll_into_view(driver, vill_el, "center")
            _select_bhulekh_dropdown(
                vill_el, value=village_id, label=village, field_name="village",
            )
            _wait_aspnet_postback(driver, wait)

            # Survey/Gat mode: default on page is सर्वे नंबर (value 17 + ddl  सर्वे नंबर) — no click needed for first phase.

            # सर्वे नंबर(भाग-1): first segment only (e.g. 113/3/1 -> 113) — ContentPlaceHolder1_txtcsno
            survey_part1 = _survey_part1_for_bhulekh(survey_number)
            if not survey_part1:
                return jsonify({"error": "survey_number must contain a non-empty first part (e.g. 113 or 113/3/1)"}), 400

            part1 = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_txtcsno')))
            _scroll_into_view(driver, part1, "center")
            part1.clear()
            part1.send_keys(survey_part1)

            search_btn = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_btnsearchfind')))
            _safe_click(driver, search_btn)
            _wait_aspnet_postback(driver, wait)

            _wait_ddlsurveyno_populated(driver, wait)
            survey_dd = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_ddlsurveyno')))
            _scroll_into_view(driver, survey_dd, "center")
            survey_option_value = _select_ddlsurveyno_exact(survey_dd, survey_number)
            _wait_aspnet_postback(driver, wait)

            mobile = _random_mobile_94()
            mobile_el = wait.until(EC.presence_of_element_located((By.ID, 'ContentPlaceHolder1_txtmobile1')))
            _scroll_into_view(driver, mobile_el, "center")
            mobile_el.clear()
            mobile_el.send_keys(mobile)
            time.sleep(0.6)

            max_captcha_attempts = 1 if captcha_manual else 5
            pdf_urls = []
            last_captcha_used = ""
            attempts_made = 0
            ocr_config_error = None

            for attempt in range(1, max_captcha_attempts + 1):
                attempts_made = attempt
                _wait_captcha_data_url(driver)

                if captcha_manual:
                    cap = _normalize_captcha(captcha_manual)
                    if not cap:
                        return jsonify({
                            "error": "captcha_manual must contain at least one letter/digit (max 6).",
                        }), 400
                else:
                    img_el = driver.find_element(
                        By.ID, "ContentPlaceHolder1_captchaImage"
                    )
                    _scroll_into_view(driver, img_el, "center")
                    time.sleep(0.25)
                    try:
                        cap = ocr_bhulekh_captcha_for_driver(img_el)
                    except RuntimeError as err:
                        try:
                            src = img_el.get_attribute("src")
                            if src and str(src).startswith("data:"):
                                png = captcha_png_bytes_from_data_src(src)
                                cap = ocr_bhulekh_captcha_png(png)
                            else:
                                ocr_config_error = str(err)
                                break
                        except Exception:
                            ocr_config_error = str(err)
                            break

                last_captcha_used = cap
                if not cap:
                    if attempt < max_captcha_attempts:
                        _refresh_bhulekh_captcha(driver)
                        continue
                    break

                captcha_el = wait.until(
                    EC.presence_of_element_located((By.ID, "ContentPlaceHolder1_txtcaptcha"))
                )
                _scroll_into_view(driver, captcha_el, "center")
                captcha_el.clear()
                captcha_el.send_keys(cap)

                submit_btn = wait.until(
                    EC.presence_of_element_located((By.ID, "ContentPlaceHolder1_btnmainsubmit"))
                )
                _safe_click(driver, submit_btn)
                time.sleep(25)
                _wait_aspnet_postback(driver, wait, seconds=1.5)

                anchors = driver.find_elements(By.XPATH, "//a[contains(@href, '.pdf')]")
                pdf_urls = [
                    h for h in (a.get_attribute("href") for a in anchors) if h
                ]
                if pdf_urls:
                    break

                if attempt < max_captcha_attempts and not captcha_manual:
                    try:
                        _refresh_bhulekh_captcha(driver)
                    except Exception:
                        pass

            current_url = driver.current_url

            if ocr_config_error:
                return jsonify({
                    "error": ocr_config_error,
                    "hint": "Install Tesseract OCR and pip install pytesseract Pillow, or pass captcha_manual=… for tests.",
                }), 503

            return jsonify({
                "success": bool(pdf_urls),
                "step": "submitted_with_pdf" if pdf_urls else "submitted_pdf_not_detected",
                "message": (
                    "Form submitted; PDF link(s) found."
                    if pdf_urls
                    else (
                        "Form submitted but no .pdf link found (wrong captcha / validation / page changed). "
                        "Try captcha_manual or increase OCR quality; check current_url."
                    )
                ),
                "current_url": current_url,
                "pdf_urls": pdf_urls,
                "captcha_attempts": attempts_made,
                "captcha_manual_used": bool(captcha_manual),
                "last_captcha_used": last_captcha_used,
                "inputs_used": {
                    "district": district,
                    "district_id": district_id,
                    "taluka": taluka,
                    "taluka_id": taluka_id,
                    "village": village,
                    "village_id": village_id,
                    "survey_number": survey_number,
                    "survey_number_part1_filled": survey_part1,
                    "survey_ddlsurveyno_value": survey_option_value,
                    "mobile_filled": mobile,
                },
            })

        finally:
            if debug_keep_browser_seconds and debug_keep_browser_seconds > 0:
                time.sleep(min(debug_keep_browser_seconds, 120))
            # added for testing purpsose, dont remove
            time.sleep(10)
            driver.quit()

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500

# Helper functions now imported from helpers/ directory

@app.route('/getPropertiesFromAPI')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_properties_from_api():
    """Get properties directly from external API without database"""
    try:
        survey_number_id = request.args.get('survey_number_id')
        print(f"Survey number id: {survey_number_id}")
        if not survey_number_id:
            return jsonify({"error": "survey_number_id is required"}), 400
        
        # Parse survey_number_id to extract survey number and village id
        # Expected format: "1_270700090077070000" where 1 is survey number and 270700090077070000 is village_id
        try:
            parts = survey_number_id.split('_')
            if len(parts) != 2:
                return jsonify({"error": "Invalid survey_number_id format. Expected: 'surveyNumber_villageId'"}), 400
            survey_number = parts[0]
            village_id = parts[1]
        except:
            return jsonify({"error": "Invalid survey_number_id format"}), 400
        
        # Get cookies from database
        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        cur.execute("SELECT data FROM cookies ORDER BY id DESC LIMIT 1")
        cookie_data = cur.fetchone()
        if not cookie_data:
            return jsonify({"error": "No cookies found in database"}), 404
        print(f"Cookie data: {cookie_data}")
        cookies = cookie_data[0]
        
        print(f"Fetching plot info for survey: {survey_number}, village: {village_id}")
        
        # Get plot info from external API
        plotInfo, cookies = getPlotInfo(survey_number, village_id, cookies)
        print(f"Plot info: {plotInfo}")

        if not plotInfo or 'plotid' not in plotInfo:
            return jsonify({"error": "Failed to fetch plot info from external API"}), 500
        
        plotId = plotInfo['plotid']
        
        # Get geo info (use cookies returned by getPlotInfo in case they were refreshed)
        geoInfo, _ = getGeoInfo(plotId, village_id, cookies)
        
        if not geoInfo:
            return jsonify({"error": "Failed to fetch geo info from external API"}), 500
        
        # Parse plot info string
        plotInfoArray = getPlotInfoFromString(plotInfo.get('info', ''))
        
        # Extract common data
        area = plotInfo.get('area', 0)
        gisCode = plotInfo.get('giscode', '')
        xmax = geoInfo.get('xmax', 0)
        xmin = geoInfo.get('xmin', 0)
        ymax = geoInfo.get('ymax', 0)
        ymin = geoInfo.get('ymin', 0)
        
        # Format response
        properties = []
        for idx, plot in enumerate(plotInfoArray):
            properties.append({
                "id": idx + 1,  # Generate sequential ID
                "plotnumber": plot['plotNumber'],
                "plot_area": str(plot['plotArea']),
                "ownername": plot['ownerName'],
                "englishownername": plot['ownerName'],  # For now, keeping same as original
                "potkharab": str(plot['potKharab']),
                "khatanumber": str(plot['khataNo']),
                "surveynumber": survey_number,
                "surveyarea": str(area),
                "plotid": plotId,
                "giscode": gisCode,
                "max_x": str(xmax),
                "min_x": str(xmin),
                "max_y": str(ymax),
                "min_y": str(ymin),
                "survey_number_id": survey_number_id
            })
        
        return jsonify(properties)
        
    except Exception as e:
        print(f"Error in get_properties_from_api: {str(e)}")
        return jsonify({"error": f"Server error: {str(e)}"}), 500


def _parse_wkt_coordinates(text):
    """Parse the numeric coordinate nesting from a WKT polygon string."""
    import re

    tokens = re.findall(r"\\(|\\)|[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?", text)
    root = []
    stack = [root]
    pair = []
    depth = 0

    for token in tokens:
        if token == "(":
            child = []
            stack[-1].append(child)
            stack.append(child)
            pair = []
            depth += 1
        elif token == ")":
            if pair:
                stack[-1].append(pair)
                pair = []
            if len(stack) > 1:
                stack.pop()
            depth -= 1
        else:
            pair.append(float(token))
            if len(pair) == 2:
                stack[-1].append(pair)
                pair = []

    return root


def _normalize_survey_geometry(raw_geometry):
    """
    Normalize the authoritative survey geometry returned by Bhu-Nakasha.

    Bhu-Nakasha may return GeoJSON as an object/JSON string or the survey
    geometry as WKT, e.g. MULTIPOLYGON (((lon lat, ...))). We convert only
    the survey-level geometry; no sub-survey geometry is inferred.
    """
    if raw_geometry is None:
        return None

    if isinstance(raw_geometry, dict):
        if raw_geometry.get("type"):
            return raw_geometry

        for key in ("geometry", "geojson", "the_geom"):
            nested = raw_geometry.get(key)
            normalized = _normalize_survey_geometry(nested)
            if normalized:
                return normalized
        return None

    if not isinstance(raw_geometry, str):
        return None

    value = raw_geometry.strip()
    if not value:
        return None

    # First handle JSON-encoded GeoJSON.
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict) and parsed.get("type"):
            return parsed
    except (TypeError, json.JSONDecodeError):
        pass

    upper = value.upper()
    if upper.startswith("MULTIPOLYGON"):
        coordinates = _parse_wkt_coordinates(value[len("MULTIPOLYGON"):])
        return {"type": "MultiPolygon", "coordinates": coordinates}

    if upper.startswith("POLYGON"):
        coordinates = _parse_wkt_coordinates(value[len("POLYGON"):])
        return {"type": "Polygon", "coordinates": coordinates}

    return None


def _normalize_survey_records(raw_info):
    """
    Normalize the sub-survey/land-record attributes returned in plotInfo['info'].

    Geometry is deliberately excluded here: the current source provides the
    survey-level geometry, but not authoritative geometry for each sub-survey.
    """
    if raw_info is None:
        return []

    if isinstance(raw_info, str):
        return [
            {
                "survey_number": item.get("plotNumber"),
                "area": item.get("plotArea"),
                "pot_kharab": item.get("potKharab"),
                "owner_name": item.get("ownerName"),
                "khata_number": item.get("khataNo"),
            }
            for item in getPlotInfoFromString(raw_info)
        ]

    if not isinstance(raw_info, list):
        return []

    records = []
    for item in raw_info:
        if not isinstance(item, dict):
            continue

        def first_value(*keys):
            for key in keys:
                if key in item and item[key] not in (None, ""):
                    return item[key]
            return None

        records.append({
            "survey_number": first_value(
                "surveyNumber", "survey_number", "plotNumber", "plotnumber",
                "Survey No.", "SurveyNo", "surveyNo"
            ),
            "area": first_value(
                "totalArea", "Total Area", "plotArea", "plot_area", "area"
            ),
            "pot_kharab": first_value(
                "potKharaba", "Pot kharaba", "potKharab", "pot_kharab"
            ),
            "owner_name": first_value(
                "ownerName", "Owner Name", "owner_name", "ownername"
            ),
            "khata_number": first_value(
                "khataNo", "Khata No.", "khata_number", "khatanumber"
            ),
            "record_type": first_value(
                "recordType", "record_type", "type", "description", "remark"
            ),
        })

    return records


@app.route('/getSurveyData')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_survey_data():
    """
    Survey-level intelligence payload for the new Land Intelligence application.

    Input:
      ?survey_number_id=<surveyNumber>_<villageId>

    This endpoint is an adapter around the existing Bhu-Nakasha acquisition
    helpers. It exposes the complete survey-level payload needed by the product
    without exposing cookies or requiring the frontend to understand the
    upstream API shape.

    Important data boundary:
      - survey geometry: authoritative and available when returned upstream
      - sub-survey attributes: available
      - sub-survey geometry: not available from this source
    """
    try:
        survey_number_id = request.args.get('survey_number_id')
        if not survey_number_id:
            return jsonify({"error": "survey_number_id is required"}), 400

        parts = survey_number_id.split('_')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return jsonify({
                "error": "Invalid survey_number_id format. Expected: 'surveyNumber_villageId'"
            }), 400

        survey_number, village_id = parts

        conn, cur = get_db_cursor(DatabaseConfig.get_config())
        cur.execute("SELECT data FROM cookies ORDER BY id DESC LIMIT 1")
        cookie_data = cur.fetchone()

        if not cookie_data:
            return jsonify({"error": "No cookies found in database"}), 404

        cookies = cookie_data[0]

        plot_info, cookies = getPlotInfo(
            survey_number,
            village_id,
            cookies,
        )

        if not plot_info or not plot_info.get("plotid"):
            return jsonify({
                "error": "Failed to fetch survey information from Bhu-Nakasha"
            }), 502

        plot_id = plot_info["plotid"]

        geo_info, _ = getGeoInfo(
            plot_id,
            village_id,
            cookies,
        )

        if not geo_info:
            return jsonify({
                "error": "Failed to fetch survey extent from Bhu-Nakasha"
            }), 502

        # Prefer geometry from the plot-info response. getExtentGeoref is used
        # for the authoritative survey bbox, not as a replacement for polygon
        # geometry.
        raw_geometry = (
            plot_info.get("the_geom")
            or plot_info.get("geometry")
            or plot_info.get("geojson")
        )
        geometry = _normalize_survey_geometry(raw_geometry)

        bbox = {
            "xmin": geo_info.get("xmin", plot_info.get("xmin")),
            "ymin": geo_info.get("ymin", plot_info.get("ymin")),
            "xmax": geo_info.get("xmax", plot_info.get("xmax")),
            "ymax": geo_info.get("ymax", plot_info.get("ymax")),
        }

        records = _normalize_survey_records(plot_info.get("info"))

        owner_names = [
            record["owner_name"]
            for record in records
            if record.get("owner_name")
        ]
        unique_owners = list(dict.fromkeys(owner_names))

        return jsonify({
            "survey": {
                "number": survey_number,
                "survey_number_id": survey_number_id,
                "village_id": village_id,
                "plot_id": plot_id,
                "gis_code": plot_info.get("giscode"),
                "area_sq_m": plot_info.get("area"),
            },
            "geometry": geometry,
            "geometry_available": geometry is not None,
            "sub_survey_geometry_available": False,
            "bbox": bbox,
            "records": records,
            "summary": {
                "record_count": len(records),
                "owner_count": len(unique_owners),
                "owners": unique_owners,
            },
            "source": {
                "provider": "Maharashtra Bhu-Nakasha",
                "survey_geometry": "getPlotInfo.the_geom",
                "survey_extent": "getExtentGeoref",
                "land_records": "getPlotInfo.info",
            },
        }), 200

    except Exception as e:
        print(f"Error in get_survey_data: {str(e)}")
        return jsonify({
            "error": f"Server error: {str(e)}"
        }), 500

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)