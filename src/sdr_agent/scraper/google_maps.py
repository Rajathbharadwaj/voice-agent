"""
Google Maps Scraper

Scrapes business listings from Google Maps search results.
Uses Selenium for dynamic content loading.
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

from .base import BaseScraper
from ..data.models import ScrapedLead


class GoogleMapsScraper(BaseScraper):
    """
    Scraper for Google Maps business listings.

    Note: Google Maps is JavaScript-heavy, so we use Selenium.
    """

    SOURCE_NAME = "google_maps"

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
        Scrape businesses from Google Maps.

        Args:
            category: Business category (e.g., "dental clinics")
            limit: Maximum number of leads to scrape

        Returns:
            List of scraped leads
        """
        driver = self._get_driver()
        leads = []

        # Build search URL
        query = f"{category} in {self.city}, {self.province}"
        url = f"https://www.google.com/maps/search/{quote_plus(query)}"

        print(f"[GoogleMaps] Searching: {query}")

        try:
            driver.get(url)

            # Wait for results to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='feed']"))
            )

            # Scroll to load more results
            feed = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
            last_count = 0
            scroll_attempts = 0

            while len(leads) < limit and scroll_attempts < 20:
                # Find all result items
                items = driver.find_elements(By.CSS_SELECTOR, "div[role='feed'] > div > div[jsaction]")

                if len(items) == last_count:
                    scroll_attempts += 1
                else:
                    scroll_attempts = 0
                    last_count = len(items)

                # Process new items
                for item in items[len(leads):]:
                    if len(leads) >= limit:
                        break

                    lead = self._parse_result_item(item, category)
                    if lead and lead.phone_number:
                        leads.append(lead)
                        print(f"[GoogleMaps] Found: {lead.business_name} - {lead.phone_number}")

                # Scroll down
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight",
                    feed
                )
                time.sleep(1)

        except TimeoutException:
            print("[GoogleMaps] Timeout waiting for results")
        except Exception as e:
            print(f"[GoogleMaps] Error: {e}")
        finally:
            pass  # Keep driver open for reuse

        print(f"[GoogleMaps] Found {len(leads)} leads")
        return leads

    def _parse_result_item(self, item, category: str) -> Optional[ScrapedLead]:
        """Parse a single result item from Google Maps."""
        try:
            # Click on item to expand details
            try:
                item.click()
                time.sleep(0.5)
            except Exception:
                pass

            # Get business name
            name_elem = item.find_element(By.CSS_SELECTOR, "div.fontHeadlineSmall")
            name = self.clean_business_name(name_elem.text.strip())

            if not name:
                return None

            # Get details panel (if expanded)
            driver = self._get_driver()

            phone = None
            address = None
            website = None

            # Try to find phone number
            try:
                # Look for phone button
                phone_buttons = driver.find_elements(
                    By.CSS_SELECTOR,
                    "button[data-item-id^='phone']"
                )
                if phone_buttons:
                    phone_text = phone_buttons[0].get_attribute("data-item-id")
                    if phone_text:
                        phone = phone_text.replace("phone:tel:", "")
            except Exception:
                pass

            # Also try parsing from the item itself
            if not phone:
                item_text = item.text
                phone_match = re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', item_text)
                if phone_match:
                    phone = phone_match.group()

            # Get address
            try:
                addr_elem = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[data-item-id='address']"
                )
                address = addr_elem.text.strip()
            except NoSuchElementException:
                # Try to extract from item text
                lines = item.text.split('\n')
                for line in lines:
                    if self.city.lower() in line.lower() or 'ab' in line.lower():
                        address = line.strip()
                        break

            # Get website
            try:
                website_btn = driver.find_element(
                    By.CSS_SELECTOR,
                    "a[data-item-id='authority']"
                )
                website = website_btn.get_attribute("href")
            except NoSuchElementException:
                pass

            # Normalize phone
            normalized_phone = self.normalize_phone(phone)

            if not normalized_phone:
                return None

            return ScrapedLead(
                business_name=name,
                phone_number=normalized_phone,
                address=address,
                city=self.city,
                category=self.normalize_category(category),
                website=website,
                source=self.SOURCE_NAME,
            )

        except Exception as e:
            return None

    def scrape_with_details(self, category: str, limit: int = 50) -> list[ScrapedLead]:
        """
        Scrape with full details by clicking each result.

        Slower but gets more complete information.
        """
        driver = self._get_driver()
        leads = []

        query = f"{category} in {self.city}, {self.province}"
        url = f"https://www.google.com/maps/search/{quote_plus(query)}"

        print(f"[GoogleMaps] Detailed search: {query}")

        try:
            driver.get(url)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='feed']"))
            )

            processed = set()
            scroll_count = 0

            while len(leads) < limit and scroll_count < 30:
                items = driver.find_elements(
                    By.CSS_SELECTOR,
                    "div[role='feed'] a[href*='/maps/place/']"
                )

                for item in items:
                    if len(leads) >= limit:
                        break

                    href = item.get_attribute("href")
                    if href in processed:
                        continue
                    processed.add(href)

                    lead = self._scrape_place_details(href, category)
                    if lead and lead.phone_number:
                        leads.append(lead)
                        print(f"[GoogleMaps] Found: {lead.business_name} - {lead.phone_number}")

                # Scroll
                feed = driver.find_element(By.CSS_SELECTOR, "div[role='feed']")
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight",
                    feed
                )
                time.sleep(1.5)
                scroll_count += 1

        except Exception as e:
            print(f"[GoogleMaps] Error: {e}")

        return leads

    def _scrape_place_details(self, place_url: str, category: str) -> Optional[ScrapedLead]:
        """Scrape details from a place page."""
        driver = self._get_driver()

        try:
            driver.get(place_url)
            time.sleep(2)

            # Get business name
            name = None
            try:
                name_elem = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "h1.DUwDvf"))
                )
                name = self.clean_business_name(name_elem.text.strip())
            except TimeoutException:
                return None

            if not name:
                return None

            # Get phone
            phone = None
            try:
                phone_btn = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[data-item-id^='phone']"
                )
                phone_attr = phone_btn.get_attribute("data-item-id")
                if phone_attr:
                    phone = phone_attr.replace("phone:tel:", "")
            except NoSuchElementException:
                pass

            # Get address
            address = None
            try:
                addr_btn = driver.find_element(
                    By.CSS_SELECTOR,
                    "button[data-item-id='address']"
                )
                address = addr_btn.text.strip()
            except NoSuchElementException:
                pass

            # Get website
            website = None
            try:
                website_link = driver.find_element(
                    By.CSS_SELECTOR,
                    "a[data-item-id='authority']"
                )
                website = website_link.get_attribute("href")
            except NoSuchElementException:
                pass

            normalized_phone = self.normalize_phone(phone)
            if not normalized_phone:
                return None

            return ScrapedLead(
                business_name=name,
                phone_number=normalized_phone,
                address=address,
                city=self.city,
                category=self.normalize_category(category),
                website=website,
                source=self.SOURCE_NAME,
            )

        except Exception as e:
            return None
