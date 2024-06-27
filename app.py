from flask import Flask
import requests
from requests.cookies import RequestsCookieJar
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

app = Flask(__name__)


@app.route('/getcookie')
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
    return {"jsession": jsession_cookie_value,"geNPRu9S" : geNPRu9S_cookie_value}