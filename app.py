from flask import Flask, json, request, jsonify
import requests
from requests.cookies import RequestsCookieJar
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from postgres.client import get_db_cursor
from flask_cors import CORS
import ast  # for literal_eval

app = Flask(__name__)
CORS(app)


@app.route('/storeCookies')
def get_cookie():
    chrome_options = Options()
    chrome_options.add_argument('--headless')

    # Initialize Chrome WebDriver with the headless option
    driver = webdriver.Chrome(options=chrome_options)

    # Open the website
    driver.get('https://mahabhunakasha.mahabhumi.gov.in/')

    # Get cookies
    cookies = driver.get_cookies()

    # Close the browser
    driver.quit()

    jsession_cookie_value = None
    geNPRu9S_cookie_value = None

    for cookie in cookies:
        if cookie['name'] == 'JSESSIONID':
            jsession_cookie_value = cookie['value']

        elif cookie['name'] == 'geNPRu9S':
            geNPRu9S_cookie_value = cookie['value']

    # store this cookie in database postgres supabase
    conn, cur = get_db_cursor({
        "user": "postgres",
        "password": "6fIoY3XJXDN0sfzs",
        "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
        "port": "5432",
        "database": "postgres"
    })
    # store it in a json based column name data
    data = {
        "jsession": jsession_cookie_value,
        "geNPRu9S": geNPRu9S_cookie_value
    }
    cur.execute("INSERT INTO cookies (data) VALUES (%s)", (json.dumps(data),))
    conn.commit()

    return True


@app.route('/getVillages')
def get_villages():
    taluka_id = request.args.get('taluka_id')  # Get taluka_id from query string
    # connect to postgres supabase and get the villages from the database
    conn, cur = get_db_cursor({
            "user": "postgres",
            "password": "6fIoY3XJXDN0sfzs",
            "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
            "port": "5432",
            "database": "postgres"
        })
    
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
            "talukaId": row_dict.get("taluka_id"),
            "surveyCount": row_dict.get("survey_count")
        })
    return jsonify(villages)

@app.route('/getSurveyNumbers')
def get_survey_numbers():
    village_id = request.args.get('village_id')
    # connect to postgres supabase and get the survey numbers from the database
    conn, cur = get_db_cursor({
        "user": "postgres",
        "password": "6fIoY3XJXDN0sfzs",
        "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
        "port": "5432",
        "database": "postgres"
    })
    
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
            ORDER BY s.number ASC
        ) subq
    """, (village_id,))
    
    result = cur.fetchone()
    return jsonify(result[0] if result[0] is not None else [])

@app.route('/getProperties')
def get_properties():
    survey_number_id = request.args.get('survey_number_id')
    conn, cur = get_db_cursor({
        "user": "postgres",
        "password": "6fIoY3XJXDN0sfzs",
        "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
        "port": "5432",
        "database": "postgres"
    })

    cur.execute("SELECT * FROM property WHERE survey_number_id = %s", (survey_number_id,))
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    properties = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        properties.append(row_dict)
    return jsonify(properties)

@app.route('/getProperty')
def get_property():
    property_id = request.args.get('property_id')
    survey_number_id = request.args.get('survey_number_id')
    village_id = request.args.get('village_id')
    
    conn, cur = get_db_cursor({
        "user": "postgres",
        "password": "6fIoY3XJXDN0sfzs",
        "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
        "port": "5432",
        "database": "postgres"
    })
    print(property_id, survey_number_id, village_id,'property_id, survey_number_id, village_id')
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

from PyPDF2 import PdfReader, PdfWriter
import io
import base64
import requests
from flask import request, jsonify

@app.route('/getPlotReportPDF')
def get_plot_report_pdf():
    try:
        giscode = request.args.get('giscode')
        plotno = request.args.get('plotno')
        
        if not giscode or not plotno:
            return jsonify({"error": "giscode and plotno are required"}), 400

        # Get cookies from database
        conn, cur = get_db_cursor({
            "user": "postgres",
            "password": "6fIoY3XJXDN0sfzs",
            "host": "db.atbjdbfjphoitfnlgrkr.supabase.co",
            "port": "5432",
            "database": "postgres"
        })
        
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
if __name__ == '__main__':
    app.debug = True
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=True)