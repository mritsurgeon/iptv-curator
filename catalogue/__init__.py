"""Canonical channel catalogue & deduplication package."""

from catalogue.catalogue_manager import CatalogueManager
from catalogue.channel_catalogue import build_canonical_record, export_channel_catalogue
from catalogue.channel_deduplicator import ChannelDeduplicator, make_stable_channel_id

__all__ = [
    "CatalogueManager",
    "ChannelDeduplicator",
    "make_stable_channel_id",
    "build_canonical_record",
    "export_channel_catalogue",
]

