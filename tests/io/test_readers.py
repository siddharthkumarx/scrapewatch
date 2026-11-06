"""File readers (build item 12)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from scrapewatch.errors import ReadError
from scrapewatch.io import detect_format, read_records


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("a.csv", "csv"),
        ("a.CSV", "csv"),
        ("a.jsonl", "jsonl"),
        ("a.ndjson", "jsonl"),
        ("a.json", "jsonl"),
        ("a.jsonl.gz", "jsonl"),
        ("a.csv.gz", "csv"),
        ("a.parquet", "parquet"),
        ("a.pq", "parquet"),
    ],
)
def test_detect_format(name: str, fmt: str) -> None:
    assert detect_format(Path(name)) == fmt


@pytest.mark.parametrize("name", ["a.txt", "noext", "a.gz"])
def test_detect_format_unknown(name: str) -> None:
    with pytest.raises(ReadError, match="--format"):
        detect_format(Path(name))


def test_csv_strings_and_empty_cells(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_text("﻿title,price,note\nA,£1.00,\nB,2,x\n", encoding="utf-8")
    assert list(read_records(p)) == [
        {"title": "A", "price": "£1.00", "note": None},
        {"title": "B", "price": "2", "note": "x"},
    ]


def test_csv_quoted_multiline_and_ragged(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_text('t,u\n"line one\nline two",x\n', encoding="utf-8")
    assert list(read_records(p)) == [{"t": "line one\nline two", "u": "x"}]
    p.write_text("t,u\na,b,c\n", encoding="utf-8")
    with pytest.raises(ReadError, match=":2: more cells"):
        list(read_records(p))


def test_jsonl_lines_blank_and_errors(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl"
    p.write_text('{"a": 1}\n\n  {"a": null}\n', encoding="utf-8")
    assert list(read_records(p)) == [{"a": 1}, {"a": None}]
    p.write_text('{"a": 1}\n{"a": \n', encoding="utf-8")
    with pytest.raises(ReadError, match=":2: not valid JSON"):
        list(read_records(p))
    p.write_text('{"a": 1}\n[1]\n', encoding="utf-8")
    with pytest.raises(ReadError, match=":2: a line must hold one JSON object"):
        list(read_records(p))


def test_json_array_file(tmp_path: Path) -> None:
    p = tmp_path / "a.json"
    p.write_text(' [{"a": 1}, {"a": 2}]', encoding="utf-8")
    assert list(read_records(p)) == [{"a": 1}, {"a": 2}]
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(ReadError, match="item 1"):
        list(read_records(p))
    p.write_text("[", encoding="utf-8")
    with pytest.raises(ReadError, match="not valid JSON"):
        list(read_records(p))


def test_gzip(tmp_path: Path) -> None:
    p = tmp_path / "a.jsonl.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"a": "é"}) + "\n")
    assert list(read_records(p)) == [{"a": "é"}]
    bad = tmp_path / "b.jsonl.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(ReadError):
        list(read_records(bad))


def test_errors_surface_early(tmp_path: Path) -> None:
    with pytest.raises(ReadError, match="no such file"):
        read_records(tmp_path / "missing.jsonl")  # not even iterated
    with pytest.raises(ReadError, match="unknown format"):
        read_records(tmp_path / "x.jsonl", fmt="xml")
    p = tmp_path / "latin.csv"
    p.write_bytes("t\ncaf\xe9\n".encode("latin-1"))
    with pytest.raises(ReadError, match="not UTF-8"):
        list(read_records(p))


def test_explicit_format_overrides_extension(tmp_path: Path) -> None:
    p = tmp_path / "data.txt"
    p.write_text('{"a": 1}\n', encoding="utf-8")
    assert list(read_records(p, "jsonl")) == [{"a": 1}]


def test_parquet_without_pyarrow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("pyarrow"):
            raise ImportError("no pyarrow")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    p = tmp_path / "a.parquet"
    p.write_bytes(b"PAR1")
    with pytest.raises(ReadError, match=r"scrapewatch\[parquet\]"):
        list(read_records(p))


@pytest.mark.extras
def test_parquet_round_trip(tmp_path: Path) -> None:
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    p = tmp_path / "a.parquet"
    pq.write_table(pa.Table.from_pylist([{"a": "x", "b": 1}, {"a": None, "b": 2}]), p)
    assert list(read_records(p)) == [{"a": "x", "b": 1}, {"a": None, "b": 2}]
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"nope")
    with pytest.raises(ReadError, match="cannot read Parquet"):
        list(read_records(bad))


def test_csv_parser_errors_become_read_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csv

    p = tmp_path / "a.csv"
    p.write_text("a\n1\n", encoding="utf-8")

    class Broken(csv.DictReader):  # type: ignore[type-arg]
        def __next__(self) -> dict[str, str]:
            raise csv.Error("bad quoting")

    monkeypatch.setattr(csv, "DictReader", Broken)
    with pytest.raises(ReadError, match="bad quoting"):
        list(read_records(p))
