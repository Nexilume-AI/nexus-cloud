"""Compatibility imports for the shared operational console."""
from . import policy
from .operational_catalog import (
    identifier, scoped, job_data, MonitoringJobsView, MonitoringJobEventsView,
    MonitoringAuditView,
)
from .operational_actions import MonitoringRequestView, MonitoringActionView
