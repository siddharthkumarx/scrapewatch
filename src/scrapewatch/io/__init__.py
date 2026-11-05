"""Readers for CSV, JSONL and Parquet (Parquet needs the ``parquet`` extra)."""

from scrapewatch.io.readers import detect_format, read_records

__all__ = ["detect_format", "read_records"]
