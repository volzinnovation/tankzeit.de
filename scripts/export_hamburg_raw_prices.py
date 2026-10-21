#!/usr/bin/env python3
"""Export historical Tankerkönig raw price rows for selected postcodes."""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

TANKER_BASE = (
    "https://data.tankerkoenig.de/"
    "tankerkoenig-organization/tankerkoenig-data/raw/branch/master"
)
FIRST_POSSIBLE_DAY = date(2000, 1, 1)
POSTCODE_MIN = 21000
POSTCODE_MAX = 21499


def data_path(prefix: str, day: date) -> str:
    return f"{prefix}/{day:%Y}/{day:%m}/{day:%Y-%m-%d}-{prefix}.csv"


def fetch(url: str) -> bytes | None:
    user = os.environ.get("TK_USER")
    password = os.environ.get("TK_PASS")
    if not user or not password:
        raise RuntimeError("TK_USER and TK_PASS environment variables are required.")

    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 4:
                raise
        time.sleep(2**attempt)
    return None


def available(day: date) -> bool:
    payload = fetch(f"{TANKER_BASE}/{data_path('prices', day)}")
    if payload is None:
        return False
    header = next(csv.reader(io.StringIO(payload.decode("utf-8-sig").splitlines()[0])))
    if "station_uuid" not in header:
        raise ValueError(f"Unexpected prices CSV header for {day}: {header}")
    return True


def find_first_available_day(last_day: date) -> date:
    anchor = next(
        (last_day - timedelta(days=offset) for offset in range(15)
         if available(last_day - timedelta(days=offset))),
        None,
    )
    if anchor is None:
        raise RuntimeError(f"No prices CSV found on or shortly before {last_day}.")

    high = anchor
    step = 1
    while True:
        candidate = max(FIRST_POSSIBLE_DAY, anchor - timedelta(days=step))
        if not available(candidate):
            low = candidate
            break
        high = candidate
        if candidate == FIRST_POSSIBLE_DAY:
            raise RuntimeError("Could not establish the beginning of the price archive.")
        step *= 2

    while (high - low).days > 1:
        middle = low + (high - low) // 2
        if available(middle):
            high = middle
        else:
            low = middle
    return high


def station_selection(as_of: date) -> tuple[set[str], bytes]:
    payload = fetch(f"{TANKER_BASE}/{data_path('stations', as_of)}")
    if payload is None:
        raise RuntimeError(f"No stations CSV found for the selection date {as_of}.")

    source = io.StringIO(payload.decode("utf-8-sig"), newline="")
    reader = csv.DictReader(source)
    uuid_column = "uuid" if "uuid" in (reader.fieldnames or []) else "station_uuid"
    if uuid_column not in (reader.fieldnames or []) or "post_code" not in (reader.fieldnames or []):
        raise ValueError(f"Unexpected stations CSV columns: {reader.fieldnames}")

    selected_rows = []
    uuids = set()
    for row in reader:
        postcode = (row.get("post_code") or "").strip()
        if postcode.isdigit() and POSTCODE_MIN <= int(postcode) <= POSTCODE_MAX:
            uuids.add(row[uuid_column])
            selected_rows.append(row)

    if not uuids:
        raise RuntimeError("No stations matched postcodes 21000–21499.")

    station_csv = io.StringIO(newline="")
    writer = csv.DictWriter(station_csv, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(selected_rows)
    return uuids, station_csv.getvalue().encode("utf-8")


def export_day(day: date, uuids: set[str]) -> tuple[date, bytes | None, int]:
    payload = fetch(f"{TANKER_BASE}/{data_path('prices', day)}")
    if payload is None:
        return day, None, 0

    source = io.StringIO(payload.decode("utf-8-sig"), newline="")
    reader = csv.reader(source)
    header = next(reader, None)
    if not header or "station_uuid" not in header:
        raise ValueError(f"Unexpected prices CSV header for {day}: {header}")
    uuid_index = header.index("station_uuid")

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    count = 0
    for row in reader:
        if len(row) > uuid_index and row[uuid_index] in uuids:
            writer.writerow(row)
            count += 1
    return day, output.getvalue().encode("utf-8"), count


def export(start_day: date, end_day: date, output_path: Path) -> None:
    uuids, station_csv = station_selection(end_day)
    days = [start_day + timedelta(days=offset) for offset in range((end_day - start_day).days + 1)]
    included_days = 0
    missing_days = 0
    included_rows = 0

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("stations.csv", station_csv)
        with ThreadPoolExecutor(max_workers=12) as executor:
            for day, payload, row_count in executor.map(lambda item: export_day(item, uuids), days):
                if payload is None:
                    missing_days += 1
                    continue
                archive.writestr(data_path("prices", day), payload)
                included_days += 1
                included_rows += row_count
                if included_days % 100 == 0:
                    print(f"Added {included_days} daily price CSVs (through {day}).", flush=True)

        manifest = {
            "source": TANKER_BASE,
            "post_code_range": f"{POSTCODE_MIN:05d}-{POSTCODE_MAX:05d}",
            "station_selection_date": end_day.isoformat(),
            "station_count": len(uuids),
            "price_data_start": start_day.isoformat(),
            "price_data_end": end_day.isoformat(),
            "included_price_files": included_days,
            "missing_price_files": missing_days,
            "matching_price_rows": included_rows,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")

    print(json.dumps(manifest, indent=2), flush=True)
    print(f"Wrote {output_path} ({output_path.stat().st_size:,} bytes).", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 3, 31))
    parser.add_argument("--output", type=Path, default=Path("hamburg-raw-prices-2026-03-31.zip"))
    args = parser.parse_args()
    start_day = find_first_available_day(args.end_date)
    print(f"First available prices CSV: {start_day}", flush=True)
    export(start_day, args.end_date, args.output)


if __name__ == "__main__":
    main()
