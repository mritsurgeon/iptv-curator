"""Database package for IPTV Curator 2.0."""

from database.database import DatabaseManager
from database.models import (
    ChannelRecord,
    EPGSourceRecord,
    ProgrammeRecord,
    SourceRecord,
    StreamEndpointRecord,
    VerificationResultRecord,
)

__all__ = [
    "DatabaseManager",
    "SourceRecord",
    "ChannelRecord",
    "StreamEndpointRecord",
    "VerificationResultRecord",
    "EPGSourceRecord",
    "ProgrammeRecord",
]

