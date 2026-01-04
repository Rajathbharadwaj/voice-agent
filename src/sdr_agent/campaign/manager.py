"""
Campaign Manager

Orchestrates outbound calling campaigns.
Manages call scheduling, concurrent calls, and progress tracking.
"""

import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass

from ..config import Config
from ..data.models import Lead, Call, Campaign, LeadStatus, CampaignStatus
from ..data.database import (
    LeadRepository,
    CallRepository,
    CampaignRepository,
    init_database,
)
from ..data.csv_logger import CSVLogger
from ..telephony.twilio_client import TwilioClient
from ..telephony.call_recovery import CallRecoveryHandler, DisconnectReason
from ..agent.sales_agent import SalesAgent, CallSession
from ..agent.call_monitor import should_skip_lead
from .business_hours import BusinessHoursChecker, should_call_lead


@dataclass
class CampaignStats:
    """Real-time campaign statistics."""
    campaign_id: str
    campaign_name: str
    status: str
    total_leads: int
    leads_called: int
    meetings_booked: int
    in_progress_calls: int
    success_rate: float  # Meeting booked rate
    avg_call_duration: float  # Seconds


class CampaignManager:
    """
    Manages a calling campaign.

    Handles:
    - Call scheduling and rate limiting
    - Concurrent call management
    - Progress tracking
    - CSV logging
    """

    def __init__(
        self,
        config: Config,
        on_call_complete: Optional[Callable[[Call], Awaitable[None]]] = None,
        respect_business_hours: bool = True,
    ):
        self.config = config
        self.on_call_complete = on_call_complete
        self.respect_business_hours = respect_business_hours

        # Initialize database
        init_database()

        # Initialize components
        self.twilio = TwilioClient(config)
        self.agent = SalesAgent(
            api_key=config.anthropic_api_key,
            model="claude-sonnet-4-20250514",
        )

        # Business hours checker
        self.hours_checker = BusinessHoursChecker()

        # Call recovery handler
        self.recovery_handler = CallRecoveryHandler(
            on_retry_scheduled=self._on_retry_scheduled,
        )

        # Campaign state
        self._current_campaign: Optional[Campaign] = None
        self._csv_logger: Optional[CSVLogger] = None
        self._running = False
        self._paused = False
        self._active_calls: dict[str, asyncio.Task] = {}
        self._call_semaphore: Optional[asyncio.Semaphore] = None
        self._skipped_leads: list[tuple[str, str, datetime]] = []  # (lead_id, reason, next_time)

    async def _on_retry_scheduled(self, lead_id: str, retry_time: datetime):
        """Callback when a retry is scheduled."""
        print(f"[Campaign] Retry scheduled for lead {lead_id} at {retry_time}")

    # =========================================================================
    # Campaign Lifecycle
    # =========================================================================

    def create_campaign(
        self,
        name: str,
        category: str,
        max_concurrent_calls: int = 3,
        calls_per_hour: int = 20,
    ) -> Campaign:
        """
        Create a new campaign.

        Args:
            name: Campaign name
            category: Target business category
            max_concurrent_calls: Max simultaneous calls
            calls_per_hour: Rate limit

        Returns:
            Created campaign
        """
        campaign = Campaign(
            name=name,
            category=category,
            max_concurrent_calls=max_concurrent_calls,
            calls_per_hour=calls_per_hour,
        )

        CampaignRepository.insert(campaign)
        print(f"[Campaign] Created campaign: {campaign.id} - {name}")

        return campaign

    def add_leads_to_campaign(
        self,
        campaign_id: str,
        lead_ids: list[str],
    ) -> int:
        """
        Add leads to a campaign.

        Args:
            campaign_id: Campaign ID
            lead_ids: List of lead IDs to add

        Returns:
            Number of leads added
        """
        added = 0
        for lead_id in lead_ids:
            LeadRepository.assign_to_campaign(lead_id, campaign_id)
            added += 1

        # Update campaign total
        campaign = CampaignRepository.get(campaign_id)
        if campaign:
            new_total = campaign.total_leads + added
            CampaignRepository.update_total_leads(campaign_id, new_total)

        print(f"[Campaign] Added {added} leads to campaign {campaign_id}")
        return added

    async def start_campaign(self, campaign_id: str):
        """
        Start running a campaign.

        Args:
            campaign_id: Campaign to start
        """
        campaign = CampaignRepository.get(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        self._current_campaign = campaign
        self._running = True
        self._paused = False

        # Initialize CSV logger
        self._csv_logger = CSVLogger(campaign_id, campaign.name)

        # Initialize semaphore for concurrent call limiting
        self._call_semaphore = asyncio.Semaphore(campaign.max_concurrent_calls)

        # Update status
        CampaignRepository.update_status(
            campaign_id,
            CampaignStatus.RUNNING,
            started_at=datetime.utcnow(),
        )

        print(f"[Campaign] Starting campaign: {campaign.name}")

        # Calculate delay between calls for rate limiting
        min_delay = 3600 / campaign.calls_per_hour  # Seconds between calls

        try:
            while self._running:
                if self._paused:
                    await asyncio.sleep(1)
                    continue

                # Get pending leads
                leads = LeadRepository.get_pending_for_campaign(campaign_id, limit=5)

                if not leads:
                    print("[Campaign] No more leads to call")
                    break

                # Process leads
                tasks = []
                for lead in leads:
                    task = asyncio.create_task(self._make_call(lead))
                    tasks.append(task)

                    # Rate limiting delay
                    await asyncio.sleep(min_delay)

                    if not self._running:
                        break

                # Wait for batch to complete
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

        except asyncio.CancelledError:
            print("[Campaign] Campaign cancelled")
        finally:
            # Clean up
            await self._cleanup()

    async def _make_call(self, lead: Lead):
        """Make a single call to a lead."""
        if not self._call_semaphore:
            return

        async with self._call_semaphore:
            if not self._running or self._paused:
                return

            # Check if lead should be skipped (do-not-call, wrong number, etc.)
            skip, skip_reason = should_skip_lead(lead.status, lead.last_outcome)
            if skip:
                print(f"[Campaign] Skipping {lead.business_name}: {skip_reason}")
                return

            # Check business hours
            if self.respect_business_hours:
                can_call, hours_reason, next_time = should_call_lead(
                    lead.category,
                    self.hours_checker,
                )
                if not can_call:
                    print(f"[Campaign] Skipping {lead.business_name}: {hours_reason}")
                    if next_time:
                        self._skipped_leads.append((lead.id, hours_reason, next_time))
                    return

            call_id = f"call_{lead.id}_{datetime.utcnow().strftime('%H%M%S')}"

            try:
                # Update lead status
                LeadRepository.update_status(lead.id, LeadStatus.CALLING)

                # Register call with recovery handler
                call_state = self.recovery_handler.register_call(
                    call_id=call_id,
                    lead_id=lead.id,
                    campaign_id=self._current_campaign.id,
                    phone_number=lead.phone_number,
                )

                # Create call record
                call = Call(
                    id=call_id,
                    lead_id=lead.id,
                    campaign_id=self._current_campaign.id,
                    phone_number=lead.phone_number,
                )

                # Start call session
                session = CallSession(
                    agent=self.agent,
                    lead=lead,
                    campaign_id=self._current_campaign.id,
                    call_id=call.id,
                )
                session.start()

                # Initiate Twilio call
                webhook_url = f"{self.config.webhook_base_url}/media-stream"
                twilio_sid = self.twilio.make_call(
                    to_number=lead.phone_number,
                    webhook_url=webhook_url.replace("https://", "wss://"),
                    metadata={
                        "lead_id": lead.id,
                        "campaign_id": self._current_campaign.id,
                        "business_name": lead.business_name,
                    },
                )

                print(f"[Campaign] Called {lead.business_name}: {twilio_sid}")

                # Note: The actual call handling happens in the media stream WebSocket
                # This just initiates the call. For a complete implementation,
                # you would need to:
                # 1. Wait for call to complete via webhook
                # 2. Get call results
                # 3. Update database

                # Simulate call duration for demo
                await asyncio.sleep(30)

                # End session (in real implementation, this happens in WebSocket handler)
                completed_call = session.end()

                # Mark call as ended normally in recovery handler
                if completed_call:
                    await self.recovery_handler.handle_normal_end(
                        call_id,
                        completed_call.outcome,
                    )

                # Log to CSV
                if self._csv_logger and completed_call:
                    updated_lead = LeadRepository.get(lead.id)
                    self._csv_logger.log_call(updated_lead, completed_call)

                # Callback
                if self.on_call_complete and completed_call:
                    await self.on_call_complete(completed_call)

            except asyncio.CancelledError:
                # Handle cancellation (e.g., campaign stopped)
                await self.recovery_handler.handle_disconnect(
                    call_id,
                    DisconnectReason.NORMAL_END,
                    "Campaign stopped",
                )
                raise

            except Exception as e:
                print(f"[Campaign] Call error for {lead.business_name}: {e}")

                # Handle unexpected error with recovery
                retry_time = await self.recovery_handler.handle_disconnect(
                    call_id,
                    DisconnectReason.UNKNOWN,
                    str(e),
                )

                if not retry_time:
                    # No retry scheduled, mark as failed
                    LeadRepository.update_status(lead.id, LeadStatus.FAILED)

    def pause_campaign(self):
        """Pause the campaign."""
        self._paused = True
        if self._current_campaign:
            CampaignRepository.update_status(
                self._current_campaign.id,
                CampaignStatus.PAUSED,
                paused_at=datetime.utcnow(),
            )
        print("[Campaign] Paused")

    def resume_campaign(self):
        """Resume a paused campaign."""
        self._paused = False
        if self._current_campaign:
            CampaignRepository.update_status(
                self._current_campaign.id,
                CampaignStatus.RUNNING,
            )
        print("[Campaign] Resumed")

    def stop_campaign(self):
        """Stop the campaign."""
        self._running = False
        print("[Campaign] Stopping...")

    async def _cleanup(self):
        """Clean up after campaign ends."""
        # Cancel any pending calls
        for task in self._active_calls.values():
            task.cancel()

        self._active_calls.clear()

        # Update campaign status
        if self._current_campaign:
            CampaignRepository.update_status(
                self._current_campaign.id,
                CampaignStatus.COMPLETED,
                completed_at=datetime.utcnow(),
            )

        self._current_campaign = None
        self._csv_logger = None

    # =========================================================================
    # Stats and Reporting
    # =========================================================================

    def get_stats(self, campaign_id: str) -> Optional[CampaignStats]:
        """Get campaign statistics."""
        campaign = CampaignRepository.get(campaign_id)
        if not campaign:
            return None

        # Get all calls for campaign
        calls = CallRepository.get_by_campaign(campaign_id, limit=1000)

        # Calculate stats
        total_duration = sum(c.duration_seconds or 0 for c in calls)
        completed_calls = [c for c in calls if c.status == "completed"]
        meetings = [c for c in calls if c.outcome == "meeting_booked"]

        avg_duration = total_duration / len(completed_calls) if completed_calls else 0
        success_rate = len(meetings) / len(completed_calls) if completed_calls else 0

        return CampaignStats(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            status=campaign.status,
            total_leads=campaign.total_leads,
            leads_called=campaign.leads_called,
            meetings_booked=campaign.meetings_booked,
            in_progress_calls=len(self._active_calls),
            success_rate=success_rate * 100,
            avg_call_duration=avg_duration,
        )

    def get_csv_path(self) -> Optional[str]:
        """Get path to the CSV log file."""
        if self._csv_logger:
            return str(self._csv_logger.path)
        return None
