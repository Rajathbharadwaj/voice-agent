#!/usr/bin/env python3
"""
Lead Scraper CLI - Full Pipeline

Scrapes Google Maps → Gets business details → Enriches with owner names via Claude Haiku

Usage:
    python -m src.sdr_agent.scrape_leads "dental clinics" Calgary --limit 5
    python -m src.sdr_agent.scrape_leads hvac Calgary --limit 10
    python -m src.sdr_agent.scrape_leads --test  # Quick test on known website
    python -m src.sdr_agent.scrape_leads --from-file leads.json  # Enrich existing list
"""

import asyncio
import argparse
import json
from pathlib import Path
from datetime import datetime

from .data.lead_scraper import LeadScraper, ScrapedLead
from .data.maps_scraper import GoogleMapsScraper


async def test_enrichment():
    """Test enrichment on a known website."""
    print("\n" + "="*60)
    print("TESTING LEAD ENRICHMENT")
    print("="*60 + "\n")

    scraper = LeadScraper(provider="anthropic")

    test_leads = [
        ScrapedLead(
            business_name="Skyview Ranch Dental Clinic",
            phone="403-266-1212",
            website="https://skyviewdentalclinic.ca/",
            address="55 Skyview Ranch Rd NE Suite 1117, Calgary, AB",
        ),
    ]

    for lead in test_leads:
        print(f"\nTesting: {lead.business_name}")
        print("-" * 40)
        enriched = await scraper.enrich_with_owner(lead)
        print(f"  Owner: {enriched.owner_name or 'NOT FOUND'}")
        print(f"  Phone: {enriched.phone}")
        print(f"  Email: {enriched.email}")
        print(f"  Address: {enriched.address}")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60 + "\n")


async def scrape_from_list(websites_file: str, output_file: str):
    """
    Scrape leads from a list of websites.

    Args:
        websites_file: JSON file with list of {business_name, website, phone?}
        output_file: Output JSON file for enriched leads
    """
    print(f"\n[CLI] Loading websites from {websites_file}")

    with open(websites_file) as f:
        websites = json.load(f)

    scraper = LeadScraper(provider="anthropic")
    results = []

    for i, site in enumerate(websites, 1):
        print(f"\n[{i}/{len(websites)}] Processing {site.get('business_name', 'Unknown')}")

        lead = ScrapedLead(
            business_name=site.get('business_name', 'Unknown'),
            phone=site.get('phone'),
            website=site.get('website'),
            address=site.get('address'),
        )

        if lead.website:
            lead = await scraper.enrich_with_owner(lead)

        results.append({
            'business_name': lead.business_name,
            'owner_name': lead.owner_name,
            'phone': lead.phone,
            'email': lead.email,
            'address': lead.address,
            'website': lead.website,
            'source': lead.source,
        })

    # Save results
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n[CLI] Saved {len(results)} leads to {output_file}")

    # Print summary
    with_owner = sum(1 for r in results if r.get('owner_name'))
    print(f"\n{'='*60}")
    print(f"SUMMARY: {with_owner}/{len(results)} leads have owner names")
    print(f"{'='*60}\n")


async def full_pipeline(category: str, location: str, limit: int, output_file: str):
    """
    Full scraping pipeline:
    1. Search Google Maps for businesses
    2. Get phone/website for each
    3. Enrich with owner names via Ollama (FREE)
    4. Save to JSON
    """
    print("\n" + "="*60)
    print(f"FULL SCRAPING PIPELINE")
    print(f"Category: {category}")
    print(f"Location: {location}")
    print(f"Limit: {limit}")
    print("="*60 + "\n")

    results = []

    # Step 1: Search Google Maps and get details
    print("[Step 1/3] Searching Google Maps and getting details...")
    async with GoogleMapsScraper(headless=True) as maps:
        listings = await maps.search(category, location, limit=limit)

        if not listings:
            print("[ERROR] No listings found on Google Maps")
            return

        print(f"[Step 1/3] Found {len(listings)} businesses")

        # Get details (phone, website) for each listing
        page = await maps.browser.new_page()
        for listing in listings:
            listing = await maps.get_listing_details(page, listing)
            print(f"    {listing.business_name}: phone={listing.phone}, website={listing.website}")
        await page.close()

    print(f"\n[Step 2/3] Enriching with owner names via Claude Haiku...")
    enricher = LeadScraper(provider="anthropic")

    for i, listing in enumerate(listings, 1):
        print(f"\n[{i}/{len(listings)}] {listing.business_name}")

        # Create ScrapedLead from MapsListing
        lead = ScrapedLead(
            business_name=listing.business_name,
            phone=listing.phone,
            address=listing.address,
            website=listing.website,
            source="google_maps",
        )

        # If we have a website, enrich with owner name
        if lead.website:
            print(f"    Website: {lead.website}")
            lead = await enricher.enrich_with_owner(lead)
        else:
            print(f"    No website found, skipping enrichment")

        results.append({
            'business_name': lead.business_name,
            'owner_name': lead.owner_name,
            'phone': enricher.normalize_phone(lead.phone),
            'email': lead.email,
            'address': lead.address,
            'website': lead.website,
            'source': lead.source,
        })

    # Step 3: Save results
    print(f"\n[Step 3/3] Saving to {output_file}...")
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    # Print summary
    with_owner = sum(1 for r in results if r.get('owner_name'))
    with_phone = sum(1 for r in results if r.get('phone'))

    print("\n" + "="*60)
    print("SCRAPING COMPLETE")
    print("="*60)
    print(f"Total businesses: {len(results)}")
    print(f"With owner name:  {with_owner}/{len(results)}")
    print(f"With phone:       {with_phone}/{len(results)}")
    print(f"Output file:      {output_file}")
    print("="*60 + "\n")

    # Print results table
    print("\nRESULTS:")
    print("-" * 80)
    for r in results:
        owner = r.get('owner_name') or 'N/A'
        phone = r.get('phone') or 'N/A'
        print(f"  {r['business_name'][:30]:<30} | Owner: {owner[:20]:<20} | {phone}")
    print("-" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape business leads and enrich with owner names via Claude Haiku",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline: Google Maps → Details → Owner extraction
  python -m src.sdr_agent.scrape_leads "dental clinics" Calgary --limit 5

  # Test enrichment on known website
  python -m src.sdr_agent.scrape_leads --test

  # Enrich leads from a JSON file
  python -m src.sdr_agent.scrape_leads --from-file leads.json
        """
    )

    parser.add_argument('category', nargs='?', help='Business category (e.g., "dental clinics", "hvac")')
    parser.add_argument('location', nargs='?', default='Calgary', help='City/location')
    parser.add_argument('--limit', type=int, default=5, help='Max results (default: 5)')
    parser.add_argument('--output', '-o', help='Output JSON file')
    parser.add_argument('--test', action='store_true', help='Run quick test')
    parser.add_argument('--from-file', help='Enrich leads from JSON file')
    parser.add_argument('--model', default='qwen2.5:7b', help='Ollama model for extraction')

    args = parser.parse_args()

    # Determine output file
    if args.output:
        output_file = args.output
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        cat_slug = args.category.replace(' ', '_') if args.category else 'leads'
        output_file = f"leads_{cat_slug}_{timestamp}.json"

    if args.test:
        asyncio.run(test_enrichment())
    elif args.from_file:
        asyncio.run(scrape_from_list(args.from_file, output_file))
    elif args.category:
        asyncio.run(full_pipeline(args.category, args.location, args.limit, output_file))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
