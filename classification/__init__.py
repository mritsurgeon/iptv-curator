"""Intelligent channel classification & scoring package."""

from classification.channel_enrichment import enrich_channel_metadata
from classification.channel_scorer import ChannelScorer
from classification.competition_mapper import CompetitionMapper
from classification.language_detector import LanguageDetector
from classification.sports_classifier import SportsClassifier

__all__ = [
    "LanguageDetector",
    "SportsClassifier",
    "CompetitionMapper",
    "ChannelScorer",
    "enrich_channel_metadata",
]

