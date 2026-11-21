"""Modular multi-source discovery engine for IPTV Curator 2.0."""

from discovery.base_provider import BaseDiscoveryProvider, CandidateChannel, CandidateEPG, DiscoveryResult
from discovery.discovery_manager import DiscoveryManager

__all__ = [
    "BaseDiscoveryProvider",
    "CandidateChannel",
    "CandidateEPG",
    "DiscoveryResult",
    "DiscoveryManager",
]

