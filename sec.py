import requests
from playwright.sync_api import sync_playwright
import time
import random
from bs4 import BeautifulSoup
import pandas as pd

def human_like_delay(min_seconds=1, max_seconds=2):
    """Simulate a human-like delay."""
    time.sleep(random.uniform(min_seconds, max_seconds))

def get_filing_html(filing_url):
    """Use ScrapeOps to retrieve the HTML content of the filing page."""
    api_key = 'ADD YOUR API KEY HERE'  # Your ScrapeOps API key
    scrapeops_url = 'https://proxy.scrapeops.io/v1/'

    try:
        response = requests.get(
            url=scrapeops_url,
            params={
                'api_key': api_key,
                'url': filing_url,
            },
        )

        if response.status_code == 200:
            return response.content
        elif response.status_code == 403:
            print(f"Failed to retrieve the filing page: {response.status_code} - Forbidden.")
            return None
        else:
            print(f"Failed to retrieve the filing page: {response.status_code}")
            return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

def is_valid_value(value):
    """Check if the extracted value is a valid float."""
    try:
        float(value.replace(',', '').replace('$', ''))
        return True
    except ValueError:
        return False

def scrape_table_from_infopage(infotable_url):
    """Scrape the table from the infotable.xml page and extract 'Name of Issuer', 'Value', and 'CUSIP'."""
    page_html = get_filing_html(infotable_url)
    if page_html:
        soup = BeautifulSoup(page_html, 'html.parser')
        table_rows = soup.find_all('tr')

        extracted_data = []
        first_row_printed = False
        for row in table_rows:
            columns = row.find_all('td')
            if len(columns) >= 5:  # Ensure there are enough columns
                name_of_issuer = columns[0].get_text(strip=True)
                cusip = columns[2].get_text(strip=True)
                value = columns[4].get_text(strip=True).replace(',', '').replace('$', '')

                if name_of_issuer and cusip and is_valid_value(value):
                    extracted_data.append((name_of_issuer, cusip, float(value)))

                    # Print the first row for each company
                    if not first_row_printed:
                        print(f"First row scraped: Name: {name_of_issuer}, CUSIP: {cusip}, Value: {value}")
                        first_row_printed = True

        return extracted_data
    else:
        print("Failed to retrieve the infotable.xml page HTML.")
        return []

def find_latest_13f_hr_and_extract_data(cik, browser, context):
    """Find the latest 13F-HR filing and extract data from the infotable.xml or similar file."""
    url = f"https://www.sec.gov/edgar/browse/?CIK={cik}"
    page = context.new_page()
    page.goto(url)

    # Wait for the filing page to load and the document to appear
    time.sleep(2)

    # Find all rows in the table with "Form type"
    rows = page.query_selector_all("table.dataTable tbody tr")

    # Simulate human-like delay before starting interaction
    human_like_delay()

    # Find the first row with "13F-HR" and get the "Filing" link
    filing_url = None
    for row in rows:
        form_type = row.query_selector("td:nth-child(1)").text_content()
        if form_type == "13F-HR":
            filing_link = row.query_selector("a.filing-link-all-files")
            filing_url = filing_link.get_attribute("href")
            break  # Exit the loop after finding the first 13F-HR

    if filing_url:
        filing_url = f"https://www.sec.gov{filing_url}"
        print(f"Found the filing URL for CIK {cik}: {filing_url}")

        # Use ScrapeOps to get the HTML of the filing page
        filing_html = get_filing_html(filing_url)

        if filing_html:
            soup = BeautifulSoup(filing_html, 'html.parser')
            
            # Locate the XML link by checking for "INFORMATION TABLE" or sequence 2
            xml_link = None
            rows = soup.find_all('tr')
            for row in rows:
                columns = row.find_all('td')
                if len(columns) >= 3:
                    description = columns[1].get_text(strip=True)
                    document_link = columns[2].find('a', href=True)
                    if description == "INFORMATION TABLE" or columns[0].get_text(strip=True) == "2":
                        if document_link:
                            xml_link = document_link['href']
                            break

            if xml_link:
                full_xml_url = f"https://www.sec.gov{xml_link}"
                print(f"Found the information table link for CIK {cik}: {full_xml_url}")

                # Scrape the table data from the identified XML page
                return scrape_table_from_infopage(full_xml_url)
            else:
                print(f"Could not find the information table link in the filing page for CIK {cik}.")
        else:
            print(f"Failed to retrieve the filing page HTML for CIK {cik}.")
    else:
        print(f"Could not find the filing link on the initial page for CIK {cik}.")

    page.close()
    return []

def aggregate_and_save_data(ciks, firm_names):
    """Aggregate data from multiple CIKs, calculate percentages, and save to a CSV."""
    aggregated_data = {}

    firm_totals = {cik: 0 for cik in ciks}  # To hold total portfolio value for each firm
    overall_totals = {}  # To hold overall total value for each stock (CUSIP)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Run in non-headless mode for better debugging
        context = browser.new_context()

        for cik in ciks:
            firm_data = find_latest_13f_hr_and_extract_data(cik, browser, context)
            firm_total_value = sum(value for _, _, value in firm_data)  # Total value of holdings for the firm
            firm_totals[cik] = firm_total_value  # Save the total value for later calculations

            for name, cusip, value in firm_data:
                if cusip in aggregated_data:
                    aggregated_data[cusip]["Number of times purchased"] += 1
                    aggregated_data[cusip]["Sum of Market Values"] += value
                else:
                    aggregated_data[cusip] = {
                        "Holding name": name,
                        "Number of times purchased": 1,
                        "Sum of Market Values": value,
                        "CUSIP": cusip
                    }

                # Calculate the Intra Stock Concentration for each firm
                if firm_totals[cik] != 0:
                    intra_stock_concentration = (value / firm_totals[cik]) * 100
                else:
                    intra_stock_concentration = 0

                # Add a column for each firm showing their Intra Stock Concentration
                if cik not in aggregated_data[cusip]:
                    aggregated_data[cusip][f"{firm_names[cik]} Intra Stock Concentration"] = 0
                aggregated_data[cusip][f"{firm_names[cik]} Intra Stock Concentration"] += value

                # Update overall totals for the stock (CUSIP)
                if cusip not in overall_totals:
                    overall_totals[cusip] = 0
                overall_totals[cusip] += value

                # Assign the Inter Stock Portfolio Concentration
                aggregated_data[cusip][f"{firm_names[cik]} Inter Stock Portfolio Concentration"] = intra_stock_concentration

        # Filter and retain only relevant data
        filtered_data = {}
        for cusip, data in aggregated_data.items():
            # Only include holdings that were purchased more than once
            if data["Number of times purchased"] > 1:
                filtered_data[cusip] = data

        # Calculate the Intra Stock Concentration
        for cusip, data in filtered_data.items():
            for cik in ciks:
                firm_column = f"{firm_names[cik]} Intra Stock Concentration"
                if firm_column in data:
                    # Check if the overall total is zero before dividing
                    if overall_totals[cusip] != 0:
                        # Intra Stock Concentration
                        data[firm_column] = (data[firm_column] / overall_totals[cusip]) * 100
                    else:
                        print(f"Warning: Overall total for CUSIP {cusip} is zero, skipping Intra Stock Concentration calculation.")
                        data[firm_column] = 0

        # Select the top 10 holdings for each firm based on Intra Stock Concentration
        top_holdings = {}
        for cik in ciks:
            firm_column = f"{firm_names[cik]} Intra Stock Concentration"
            firm_holdings = sorted(
                [(cusip, data) for cusip, data in filtered_data.items() if firm_column in data],
                key=lambda x: x[1][firm_column],
                reverse=True
            )[:10]

            for cusip, data in firm_holdings:
                top_holdings[cusip] = data

        # Calculate Inter Stock Percentage / Largest Firm Holding Percentage for each CUSIP
        for cusip, data in top_holdings.items():
            for cik in ciks:
                firm_column = f"{firm_names[cik]} Inter Stock Portfolio Concentration"
                if firm_column in data:
                    max_concentration = max(
                        top_holdings[cusip].get(f"{firm_names[other_cik]} Intra Stock Concentration", 0)
                        for other_cik in ciks
                    )
                    if max_concentration > 0:
                        data[f"{firm_names[cik]} Inter Stock % / Largest Firm Holding %"] = data[firm_column] / max_concentration * 100
                    else:
                        data[f"{firm_names[cik]} Inter Stock % / Largest Firm Holding %"] = 0

        context.close()
        browser.close()

    # Convert the top holdings data to a DataFrame
    df = pd.DataFrame.from_dict(top_holdings, orient='index')

    # Save the DataFrame to a CSV file
    df.to_csv('filtered_top_holdings.csv', index=False)
    print("Filtered data saved to filtered_top_holdings.csv")

# Example usage
ciks = ["1466153", "860561", "1654344"]  # New CIKs provided
firm_names = {
    "1466153": "Two Sigma",
    "860561": "Spyglass Capital",
    "1654344": "TCI Fund Management"
}
aggregate_and_save_data(ciks, firm_names)
