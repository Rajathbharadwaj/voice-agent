"""
Yelp Scraper

Scrapes business listings from Yelp search results.
Uses Selenium for dynamic content and BeautifulSoup for parsing.
"""

import time
import re
from typing import Optional
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..data.models import ScrapedLead


class YelpScraper(BaseScraper):
    """
    Scraper for Yelp business listings.

    Yelp Canada uses yelp.ca domain.
    """

    SOURCE_NAME = "yelp"
    BASE_URL = "https://www.yelp.ca"

    def __init__(
        self,
        city: str = "Calgary",
        province: str = "AB",
        headless: bool = True,
    ):
        super().__init__(city, province)
        self.headless = headless
        self._driver = None

    def _get_driver(self) -> webdriver.Chrome:
        """Get or create Chrome WebDriver."""
        if self._driver is None:
            options = Options()
            if self.headless:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

            service = Service(ChromeDriverManager().install())
            self._driver = webdriver.Chrome(service=service, options=options)

        return self._driver

    def close(self):
        """Close the browser."""
        if self._driver:
            self._driver.quit()
            self._driver = None

    def scrape(self, category: str, limit: int = 50) -> list[ScrapedLead]:
        """
        Scrape businesses from Yelp.

        Args:
            category: Business category (e.g., "dental clinics")
            limit: Maximum number of leads to scrape

        Returns:
            List of scraped leads
        """
        driver = self._get_driver()
        leads = []
        page = 0

        print(f"[Yelp] Searching: {category} in {self.city}")

        while len(leads) < limit:
            # Build search URL
            location = f"{self.city}, {self.province}"
            start = page * 10
            url = f"{self.BASE_URL}/search?find_desc={quote_plus(category)}&find_loc={quote_plus(location)}&start={start}"

            try:
                driver.get(url)
                time.sleep(2)

                # Check for end of results
                if "No results for" in driver.page_source:
                    print("[Yelp] No more results")
                    break

                # Parse the page
                soup = BeautifulSoup(driver.page_source, 'html.parser')

                # Find business cards
                results = soup.select('div[data-testid="serp-ia-card"]')

                if not results:
                    # Try alternate selector
                    results = soup.select('li.y-css-1p7bp3k')

                if not results:
                    print(f"[Yelp] No results on page {page}")
                    break

                page_leads = 0
                for result in results:
                    if len(leads) >= limit:
                        break

                    lead = self._parse_search_result(result, category)
                    if lead:
                        # Get phone from detail page
                        lead = self._enrich_from_detail_page(lead, driver)
                        if lead and lead.phone_number:
                            leads.append(lead)
                            page_leads += 1
                            print(f"[Yelp] Found: {lead.business_name} - {lead.phone_number}")

                if page_leads == 0:
                    break

                page += 1

            except Exception as e:
                print(f"[Yelp] Error on page {page}: {e}")
                break

        print(f"[Yelp] Found {len(leads)} leads total")
        return leads

    def _parse_search_result(self, result, category: str) -> Optional[ScrapedLead]:
        """Parse a search result card."""
        try:
            # Get business name
            name_elem = result.select_one('a[class*="businessName"]')
            if not name_elem:
                name_elem = result.select_one('h3 a')
            if not name_elem:
                name_elem = result.select_one('a.y-css-hcgwj4')

            if not name_elem:
                return None

            name = self.clean_business_name(name_elem.text.strip())
            if not name:
                return None

            # Get detail page URL
            detail_url = name_elem.get('href', '')
            if detail_url and not detail_url.startswith('http'):
                detail_url = f"{self.BASE_URL}{detail_url}"

            # Get address
            address = None
            addr_elem = result.select_one('span.y-css-qf1uh1')
            if addr_elem:
                address = addr_elem.text.strip()

            return ScrapedLead(
                business_name=name,
                phone_number=None,  # Will be fetched from detail page
                address=address,
                city=self.city,
                category=self.normalize_category(category),
                website=detail_url,  # Temporarily store detail URL
                source=self.SOURCE_NAME,
            )

        except Exception as e:
            return None

    def _enrich_from_detail_page(
        self,
        lead: ScrapedLead,
        driver: webdriver.Chrome
    ) -> Optional[ScrapedLead]:
        """Get phone number and website from detail page."""
        detail_url = lead.website
        if not detail_url or '/biz/' not in detail_url:
            return None

        try:
            driver.get(detail_url)
            time.sleep(1.5)

            soup = BeautifulSoup(driver.page_source, 'html.parser')

            # Get phone number
            phone = None

            # Try phone link
            phone_link = soup.select_one('a[href^="tel:"]')
            if phone_link:
                phone = phone_link.get('href', '').replace('tel:', '')

            # Try phone text pattern
            if not phone:
                page_text = soup.get_text()
                phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', page_text)
                if phone_match:
                    phone = phone_match.group()

            # Get actual website (not Yelp page)
            website = None
            website_elem = soup.select_one('a[href*="biz_redir"]')
            if website_elem:
                website = website_elem.get('href')
                # Extract actual URL from redirect
                if 'url=' in website:
                    import urllib.parse
                    parsed = urllib.parse.urlparse(website)
                    params = urllib.parse.parse_qs(parsed.query)
                    if 'url' in params:
                        website = params['url'][0]

            # Normalize phone
            normalized_phone = self.normalize_phone(phone)

            if not normalized_phone:
                return None

            return ScrapedLead(
                business_name=lead.business_name,
                phone_number=normalized_phone,
                address=lead.address,
                city=lead.city,
                category=lead.category,
                website=website,
                source=self.SOURCE_NAME,
            )

        except Exception as e:
            return None


class YelpAPIAlternative:
    """
    Alternative Yelp data fetcher using their public-facing endpoints.

    Note: This is for educational purposes. For production use,
    please use Yelp's official Fusion API.
    """

    def __init__(self, city: str = "Calgary", province: str = "AB"):
        self.city = city
        self.province = province

    async def search(self, category: str, limit: int = 20):
        """
        Search Yelp using httpx.

        This demonstrates how to use the public search endpoint.
        For production, use the Yelp Fusion API with an API key.
        """
        import httpx

        location = f"{self.city}, {self.province}"
        url = f"https://www.yelp.ca/search/snippet"
        params = {
            "find_desc": category,
            "find_loc": location,
            "request_origin": "user",
        }

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, headers=headers)
            if response.status_code == 200:
                return response.json()
            return None
