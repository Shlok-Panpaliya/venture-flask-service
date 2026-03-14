"""
Data parsing helper functions for processing API responses
"""
import re


def getPlotInfoFromString(input_string):
    """Parse plot info string into structured data"""
    pattern = r"Survey No\.\s*:\s*([\d\/\u0900-\u097F]+)\s*Total Area\s*:\s*([\d\.]+)\s*Pot kharaba\s*:\s*([\d\.]+)\s*Owner Name\s*:\s*([^\n]+)\s*Khata No\.\s*:\s*(\d+)"
    matches = re.findall(pattern, input_string)

    survey_data = []
    for match in matches:
        survey_data.append({
            "plotNumber": match[0],
            "plotArea": float(match[1]),
            "potKharab": float(match[2]),
            "ownerName": match[3].strip(),
            "khataNo": int(match[4])
        })

    return survey_data
