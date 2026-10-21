from __future__ import annotations

import csv
import io
import unittest
from datetime import date
from unittest.mock import patch

from scripts.export_hamburg_raw_prices import (
    export_day,
    find_first_available_day,
    station_selection,
)


class HamburgRawPriceExportTests(unittest.TestCase):
    @patch("scripts.export_hamburg_raw_prices.available")
    def test_finds_first_available_day(self, mock_available) -> None:
        mock_available.side_effect = lambda day: day >= date(2024, 1, 1)

        self.assertEqual(find_first_available_day(date(2024, 1, 5)), date(2024, 1, 1))

    @patch("scripts.export_hamburg_raw_prices.fetch")
    def test_station_selection_filters_postcodes(self, mock_fetch) -> None:
        mock_fetch.return_value = (
            b"uuid,post_code,city\n"
            b"station-1,21000,Hamburg\n"
            b"station-2,21499,Winsen\n"
            b"station-3,21500,Outside\n"
        )

        uuids, stations = station_selection(date(2026, 3, 31))
        station_rows = list(csv.DictReader(io.StringIO(stations.decode())))

        self.assertEqual(uuids, {"station-1", "station-2"})
        self.assertEqual([row["uuid"] for row in station_rows], ["station-1", "station-2"])

    @patch("scripts.export_hamburg_raw_prices.fetch")
    def test_export_day_retains_only_selected_station_rows(self, mock_fetch) -> None:
        mock_fetch.return_value = (
            b"station_uuid,date,diesel\n"
            b"station-1,2026-03-31T10:00:00+01:00,1.80\n"
            b"station-2,2026-03-31T10:01:00+01:00,1.81\n"
        )

        day, payload, row_count = export_day(date(2026, 3, 31), {"station-2"})
        rows = list(csv.reader(io.StringIO(payload.decode())))

        self.assertEqual(day, date(2026, 3, 31))
        self.assertEqual(row_count, 1)
        self.assertEqual(rows, [
            ["station_uuid", "date", "diesel"],
            ["station-2", "2026-03-31T10:01:00+01:00", "1.81"],
        ])


if __name__ == "__main__":
    unittest.main()
