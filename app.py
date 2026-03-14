from flask import Flask, json, request, jsonify
import requests
from requests.cookies import RequestsCookieJar
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from postgres.client import get_db_cursor
from config import DatabaseConfig
from flask_cors import CORS
import ast  # for literal_eval
import time  # for sleep functionality
from PyPDF2 import PdfReader, PdfWriter
import io
import base64
import requests
from flask import request, jsonify
import re
import json
from helpers.api_client import getPlotInfo, getGeoInfo
from helpers.data_parser import getPlotInfoFromString
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
    geNPRu9S_cookie_value = session.cookies.get("geNPRu9S")
    return jsession_cookie_value, geNPRu9S_cookie_value


@app.route('/storeCookies')
def get_cookie():
    # Use requests instead of Selenium: Vercel has read-only filesystem and no Chrome,
    # so Selenium/ChromeDriver cannot run. HTTP-based cookie fetch works on serverless.
    try:
        jsession_cookie_value, geNPRu9S_cookie_value = _fetch_cookies_via_requests()
    except RuntimeError as e:
        return jsonify({"message": str(e), "code": 500}), 500

    if not jsession_cookie_value and not geNPRu9S_cookie_value:
        return jsonify({
            "message": "No JSESSIONID or geNPRu9S cookies received. The site may set them via JavaScript; run /storeCookies on a host with Chrome (e.g. Lambda with chrome layer) or use a remote browser service.",
            "code": 500,
        }), 500

    conn, cur = get_db_cursor(DatabaseConfig.get_config())
    cur.execute("DELETE FROM cookies")
    data = {
        "jsession": jsession_cookie_value,
        "geNPRu9S": geNPRu9S_cookie_value,
    }
    cur.execute("INSERT INTO cookies (data) VALUES (%s)", (json.dumps(data),))
    conn.commit()

    return jsonify({"message": "Cookies stored successfully", "code": 200})


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
            'geNPRu9S': cookies['geNPRu9S'],
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

@app.route('/get712PDF')
@cache.cached(timeout=24 * 60 * 60, query_string=True)
def get_7_12_pdf():
    try:
        # Get parameters from request
        district = request.args.get('district', 'पुणे')  # Default to Pune
        taluka = request.args.get('taluka')
        village = request.args.get('village')
        survey_number = request.args.get('survey_number')
        mobile = request.args.get('mobile', '9876543210')  # Default dummy mobile
        
        if not all([taluka, village, survey_number]):
            return jsonify({"error": "taluka, village, and survey_number are required"}), 400

        # Setup Chrome driver
        chrome_options = Options()
       # chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        
        driver = webdriver.Chrome(options=chrome_options)
        
        try:
            # Navigate to the website
            driver.get('https://bhulekh.mahabhumi.gov.in/')
            
            # Wait for page to load
            driver.implicitly_wait(10)
            
            # Select 7/12 option
            # seven_twelve_radio = driver.find_element("xpath", "//input[@value='7/12']")
            # seven_twelve_radio.click()
            
            # Select district
            district_dropdown = driver.find_element("id", "ContentPlaceHolder1_ddlMainDist")
            district_dropdown.click()

            district_option = district_dropdown.select_by_visible_text("अमरावती")
            district_option.click()
            
            # Wait for taluka dropdown to populate
            import time
            time.sleep(2)
            
            # Select taluka
            taluka_dropdown = driver.find_element("name", "taluka")
            taluka_dropdown.click()
            taluka_option = driver.find_element("xpath", f"//option[contains(text(), '{taluka}')]")
            taluka_option.click()
            
            # Wait for village dropdown to populate
            time.sleep(2)
            
            # Select village
            village_dropdown = driver.find_element("name", "village")
            village_dropdown.click()
            village_option = driver.find_element("xpath", f"//option[contains(text(), '{village}')]")
            village_option.click()
            
            # Select survey number option (assuming "सर्वे नंबर" - Survey Number)
            survey_radio = driver.find_element("xpath", "//input[@value='survey']")
            survey_radio.click()
            
            # Enter survey number
            survey_input = driver.find_element("name", "survey_number")
            survey_input.clear()
            survey_input.send_keys(survey_number)
            
            # Enter mobile number
            mobile_input = driver.find_element("name", "mobile")
            mobile_input.clear()
            mobile_input.send_keys(mobile)
            
            # Select language (default to Marathi)
            language_dropdown = driver.find_element("name", "language")
            language_dropdown.click()
            marathi_option = driver.find_element("xpath", "//option[@value='Marathi']")
            marathi_option.click()
            
            # Handle captcha - for now, we'll try to detect and solve simple captchas
            captcha_img = driver.find_element("xpath", "//img[contains(@src, 'captcha')]")
            captcha_input = driver.find_element("name", "captcha")
            
            # Simple captcha detection (this is a basic implementation)
            # In a real scenario, you might need more sophisticated captcha solving
            captcha_text = "12345"  # Placeholder - would need actual captcha solving
            captcha_input.clear()
            captcha_input.send_keys(captcha_text)
            
            # Submit the form
            submit_button = driver.find_element("xpath", "//input[@type='submit']")
            submit_button.click()
            
            # Wait for results
            time.sleep(5)
            
            # Check if we got results or need to handle errors
            current_url = driver.current_url
            page_source = driver.page_source
            
            # Look for download link or PDF content
            pdf_links = driver.find_elements("xpath", "//a[contains(@href, '.pdf')]")
            
            if pdf_links:
                pdf_url = pdf_links[0].get_attribute('href')
                return jsonify({
                    "success": True,
                    "pdf_url": pdf_url,
                    "message": "7/12 record found successfully"
                })
            else:
                # Check for error messages
                error_elements = driver.find_elements("xpath", "//*[contains(text(), 'Error') or contains(text(), 'त्रुटी')]")
                if error_elements:
                    error_message = error_elements[0].text
                    return jsonify({
                        "success": False,
                        "error": f"Website error: {error_message}"
                    }), 400
                else:
                    return jsonify({
                        "success": False,
                        "message": "No PDF found, but form submitted successfully",
                        "current_url": current_url
                    })
            
        finally:
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

        cookies = cookie_data[0]
        
        print(f"Fetching plot info for survey: {survey_number}, village: {village_id}")
        
        # Get plot info from external API
        plotInfo = getPlotInfo(survey_number, village_id, cookies)

        if not plotInfo or 'plotid' not in plotInfo:
            return jsonify({"error": "Failed to fetch plot info from external API"}), 500
        
        plotId = plotInfo['plotid']
        
        # Get geo info
        geoInfo = getGeoInfo(plotId, village_id, cookies)
        
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

if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)