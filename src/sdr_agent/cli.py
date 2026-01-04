"""
SDR Agent CLI

Command-line interface for managing leads, campaigns, and calls.
"""

# Fix perth watermarker issue before any chatterbox imports
try:
    import perth
    if hasattr(perth, 'DummyWatermarker'):
        perth.PerthImplicitWatermarker = perth.DummyWatermarker
except Exception:
    pass  # Skip if perth module structure has changed

import asyncio
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from .config import Config, load_config
from .data.database import (
    init_database,
    LeadRepository,
    CallRepository,
    CampaignRepository,
)
from .data.models import Lead, Campaign, LeadStatus
from .data.csv_logger import export_leads_to_csv
from .scraper import GoogleMapsScraper, YelpScraper
from .campaign import CampaignManager

console = Console()


@click.group()
@click.pass_context
def cli(ctx):
    """SDR Agent - AI-powered outbound sales calling."""
    ctx.ensure_object(dict)
    init_database()


# =============================================================================
# Lead Commands
# =============================================================================

@cli.group()
def leads():
    """Manage leads."""
    pass


@leads.command("scrape")
@click.argument("category")
@click.option("--source", "-s", type=click.Choice(["google", "yelp", "all"]), default="all")
@click.option("--limit", "-l", default=50, help="Maximum leads to scrape")
@click.option("--city", default="Calgary", help="City to search")
def scrape_leads(category: str, source: str, limit: int, city: str):
    """Scrape leads for a category (e.g., 'dental clinics')."""
    console.print(f"[bold blue]Scraping {category} in {city}...[/]")

    all_leads = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        if source in ["google", "all"]:
            task = progress.add_task("Google Maps...", total=None)
            try:
                scraper = GoogleMapsScraper(city=city, headless=True)
                leads = scraper.scrape(category, limit=limit)
                all_leads.extend(leads)
                progress.update(task, description=f"Google Maps: {len(leads)} found")
            except Exception as e:
                progress.update(task, description=f"Google Maps: Error - {e}")
            finally:
                try:
                    scraper.close()
                except:
                    pass

        if source in ["yelp", "all"]:
            task = progress.add_task("Yelp...", total=None)
            try:
                scraper = YelpScraper(city=city, headless=True)
                leads = scraper.scrape(category, limit=limit)
                all_leads.extend(leads)
                progress.update(task, description=f"Yelp: {len(leads)} found")
            except Exception as e:
                progress.update(task, description=f"Yelp: Error - {e}")
            finally:
                try:
                    scraper.close()
                except:
                    pass

    # Deduplicate by phone number
    seen_phones = set()
    unique_leads = []
    for scraped in all_leads:
        if scraped.phone_number not in seen_phones:
            seen_phones.add(scraped.phone_number)
            unique_leads.append(scraped)

    # Save to database
    saved = 0
    for scraped in unique_leads:
        lead = Lead(
            business_name=scraped.business_name,
            phone_number=scraped.phone_number,
            address=scraped.address,
            city=scraped.city,
            category=scraped.category,
            website=scraped.website,
            source=scraped.source,
        )
        if LeadRepository.insert(lead):
            saved += 1

    console.print(f"\n[green]Saved {saved} new leads to database[/]")
    console.print(f"[dim]({len(all_leads)} found, {len(unique_leads)} unique, {len(unique_leads) - saved} duplicates)[/]")


@leads.command("list")
@click.option("--category", "-c", help="Filter by category")
@click.option("--status", "-s", help="Filter by status")
@click.option("--limit", "-l", default=50)
def list_leads(category: Optional[str], status: Optional[str], limit: int):
    """List leads in the database."""
    leads = LeadRepository.get_all(category=category, status=status, limit=limit)

    if not leads:
        console.print("[yellow]No leads found[/]")
        return

    table = Table(title=f"Leads ({len(leads)} shown)")
    table.add_column("ID", style="dim")
    table.add_column("Business")
    table.add_column("Phone")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Outcome")

    for lead in leads:
        table.add_row(
            lead.id[:8],
            lead.business_name[:30],
            lead.phone_number,
            lead.category or "-",
            lead.status,
            lead.last_outcome or "-",
        )

    console.print(table)


@leads.command("categories")
def list_categories():
    """List all lead categories."""
    categories = LeadRepository.get_categories()

    if not categories:
        console.print("[yellow]No categories found[/]")
        return

    table = Table(title="Lead Categories")
    table.add_column("Category")
    table.add_column("Count", justify="right")

    for cat in categories:
        count = LeadRepository.count_by_category(cat)
        table.add_row(cat, str(count))

    console.print(table)


@leads.command("export")
@click.option("--category", "-c", help="Filter by category")
@click.option("--output", "-o", default="leads_export.csv", help="Output file")
def export_leads(category: Optional[str], output: str):
    """Export leads to CSV."""
    leads = LeadRepository.get_all(category=category, limit=10000)

    if not leads:
        console.print("[yellow]No leads to export[/]")
        return

    from pathlib import Path
    filepath = Path(output)
    export_leads_to_csv(leads, filepath)
    console.print(f"[green]Exported {len(leads)} leads to {filepath}[/]")


# =============================================================================
# Campaign Commands
# =============================================================================

@cli.group()
def campaign():
    """Manage campaigns."""
    pass


@campaign.command("create")
@click.argument("name")
@click.argument("category")
@click.option("--concurrent", "-c", default=3, help="Max concurrent calls")
@click.option("--rate", "-r", default=20, help="Calls per hour")
def create_campaign(name: str, category: str, concurrent: int, rate: int):
    """Create a new campaign."""
    config = load_config()
    manager = CampaignManager(config)

    campaign = manager.create_campaign(
        name=name,
        category=category,
        max_concurrent_calls=concurrent,
        calls_per_hour=rate,
    )

    console.print(f"[green]Created campaign: {campaign.id}[/]")
    console.print(f"  Name: {campaign.name}")
    console.print(f"  Category: {campaign.category}")


@campaign.command("list")
def list_campaigns():
    """List all campaigns."""
    campaigns = CampaignRepository.get_all()

    if not campaigns:
        console.print("[yellow]No campaigns found[/]")
        return

    table = Table(title="Campaigns")
    table.add_column("ID", style="dim")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Leads")
    table.add_column("Called")
    table.add_column("Meetings")

    for c in campaigns:
        table.add_row(
            c.id,
            c.name,
            c.category,
            c.status,
            str(c.total_leads),
            str(c.leads_called),
            str(c.meetings_booked),
        )

    console.print(table)


@campaign.command("add-leads")
@click.argument("campaign_id")
@click.option("--category", "-c", help="Add leads from this category")
@click.option("--limit", "-l", default=100, help="Maximum leads to add")
def add_leads_to_campaign(campaign_id: str, category: Optional[str], limit: int):
    """Add leads to a campaign."""
    # Get leads
    leads = LeadRepository.get_all(category=category, status="new", limit=limit)

    if not leads:
        console.print("[yellow]No new leads found[/]")
        return

    # Add to campaign
    config = load_config()
    manager = CampaignManager(config)

    lead_ids = [lead.id for lead in leads]
    added = manager.add_leads_to_campaign(campaign_id, lead_ids)

    console.print(f"[green]Added {added} leads to campaign {campaign_id}[/]")


@campaign.command("start")
@click.argument("campaign_id")
def start_campaign(campaign_id: str):
    """Start a campaign."""
    config = load_config()

    # Validate config
    if not config.twilio_account_sid or not config.twilio_auth_token:
        console.print("[red]Error: Twilio credentials not configured[/]")
        console.print("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env")
        return

    if not config.anthropic_api_key:
        console.print("[red]Error: Anthropic API key not configured[/]")
        console.print("Set ANTHROPIC_API_KEY in .env")
        return

    manager = CampaignManager(config)

    console.print(f"[bold blue]Starting campaign {campaign_id}...[/]")
    console.print("[dim]Press Ctrl+C to stop[/]")

    try:
        asyncio.run(manager.start_campaign(campaign_id))
    except KeyboardInterrupt:
        manager.stop_campaign()
        console.print("\n[yellow]Campaign stopped[/]")


@campaign.command("status")
@click.argument("campaign_id")
def campaign_status(campaign_id: str):
    """Get campaign status."""
    config = load_config()
    manager = CampaignManager(config)

    stats = manager.get_stats(campaign_id)
    if not stats:
        console.print(f"[red]Campaign not found: {campaign_id}[/]")
        return

    panel = Panel(
        f"""[bold]{stats.campaign_name}[/] ({stats.campaign_id})

Status: {stats.status}
Total Leads: {stats.total_leads}
Leads Called: {stats.leads_called}
Meetings Booked: [green]{stats.meetings_booked}[/]
Success Rate: {stats.success_rate:.1f}%
Avg Call Duration: {stats.avg_call_duration:.1f}s
Active Calls: {stats.in_progress_calls}
""",
        title="Campaign Status",
    )
    console.print(panel)


# =============================================================================
# Server Commands
# =============================================================================

@cli.command("serve")
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8080)
def serve(host: str, port: int):
    """Start the webhook server for Twilio."""
    console.print(f"[bold blue]Starting SDR Agent server on {host}:{port}[/]")

    from .server import create_app
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host, port=port)


# =============================================================================
# Setup Commands
# =============================================================================

@cli.command("init")
def init():
    """Initialize the database and directories."""
    from .config import DATA_DIR, EXPORTS_DIR

    init_database()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    console.print("[green]Database initialized[/]")
    console.print(f"  Data directory: {DATA_DIR}")
    console.print(f"  Exports directory: {EXPORTS_DIR}")


@cli.command("config")
def show_config():
    """Show current configuration."""
    config = load_config()

    console.print(Panel(
        f"""[bold]SDR Agent Configuration[/]

Twilio Account: {'[green]Configured[/]' if config.twilio_account_sid else '[red]Not set[/]'}
Twilio Phone: {config.twilio_phone_number or '[red]Not set[/]'}
Anthropic API: {'[green]Configured[/]' if config.anthropic_api_key else '[red]Not set[/]'}
Webhook URL: {config.webhook_base_url}
""",
        title="Configuration",
    ))


if __name__ == "__main__":
    cli()
