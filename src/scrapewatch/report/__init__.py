"""HTML report and JSON summary."""

from scrapewatch.report.html import render_html, report_paths, write_html
from scrapewatch.report.summary import read_summary, write_summary

__all__ = ["read_summary", "render_html", "report_paths", "write_html", "write_summary"]
