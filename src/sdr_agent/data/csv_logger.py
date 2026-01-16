"""CSV logger for call outcomes."""
import csv
import os
from datetime import datetime
from pathlib import Path


# Default log directory
LOG_DIR = Path(__file__).parent.parent.parent.parent / "data" / "call_logs"


class CSVLogger:
    """Legacy CSV logger for sales calls."""
    def __init__(self, *args, **kwargs): pass
    def log(self, *args, **kwargs): pass
    def log_call(self, *args, **kwargs): pass


def export_leads_to_csv(*args, **kwargs):
    """Stub for exporting leads to CSV."""
    pass


class HealthcareCallLogger:
    """CSV logger for healthcare appointment calls."""

    HEADERS = [
        "timestamp",
        "call_sid",
        "patient_name",
        "phone_number",
        "appointment_date",
        "appointment_time",
        "provider_name",
        "clinic_name",
        "appointment_type",
        "outcome",
        "preferred_reschedule_date",
        "preferred_reschedule_time",
        "reschedule_reason",
        "notes",
    ]

    def __init__(self, log_dir: Path = None):
        self.log_dir = log_dir or LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "healthcare_calls.csv"
        self._ensure_headers()

    def _ensure_headers(self):
        """Create CSV with headers if it doesn't exist."""
        if not self.log_file.exists():
            with open(self.log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(self.HEADERS)

    def log_call(
        self,
        call_sid: str,
        patient_name: str,
        phone_number: str,
        appointment_date: str,
        appointment_time: str,
        provider_name: str,
        clinic_name: str,
        appointment_type: str,
        outcome: str,
        preferred_reschedule_date: str = "",
        preferred_reschedule_time: str = "",
        reschedule_reason: str = "",
        notes: str = "",
    ):
        """Log a healthcare call outcome to CSV."""
        row = [
            datetime.now().isoformat(),
            call_sid,
            patient_name,
            phone_number,
            appointment_date,
            appointment_time,
            provider_name,
            clinic_name,
            appointment_type,
            outcome,
            preferred_reschedule_date,
            preferred_reschedule_time,
            reschedule_reason,
            notes,
        ]

        with open(self.log_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        print(f"[CSVLogger] Logged call outcome: {patient_name} - {outcome}")
        return self.log_file


# Singleton instance for easy access
_healthcare_logger = None


def get_healthcare_logger() -> HealthcareCallLogger:
    """Get singleton healthcare call logger."""
    global _healthcare_logger
    if _healthcare_logger is None:
        _healthcare_logger = HealthcareCallLogger()
    return _healthcare_logger


def log_healthcare_call(context) -> Path:
    """
    Log a healthcare call from a HealthcareCallContext object.

    Args:
        context: HealthcareCallContext with call details and outcome

    Returns:
        Path to the log file
    """
    logger = get_healthcare_logger()

    notes = "; ".join(context.notes) if context.notes else ""

    return logger.log_call(
        call_sid=context.call_sid or "",
        patient_name=context.patient_name,
        phone_number=context.phone_number,
        appointment_date=context.appointment_date,
        appointment_time=context.appointment_time,
        provider_name=context.provider_name,
        clinic_name=context.clinic_name,
        appointment_type=context.appointment_type,
        outcome=context.outcome or "unknown",
        preferred_reschedule_date=context.preferred_date or "",
        preferred_reschedule_time=context.preferred_time or "",
        reschedule_reason=context.reschedule_reason or "",
        notes=notes,
    )
