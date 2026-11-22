"""Grocery providers: where products, live prices and (later) checkout come from.

A provider turns a search query into `Offer`s. The matcher then picks packs and
prices exactly as it does for the built-in catalogue, so every provider gets the
same pack gauges, leftovers and unmatched handling.
"""
from .base import Location, Offer, Provider

__all__ = ["Location", "Offer", "Provider"]
