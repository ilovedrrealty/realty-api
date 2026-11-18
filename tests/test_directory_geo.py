"""Read-only reference semantics; no network, geocoding or stored coordinates."""

import unittest

from pydantic import ValidationError

from schemas.directory_maps import DirectoryMapArea, DirectoryMapSummary
from utils.directory_geo import (
    AREAS, AREA_KEYS, CATALOG, SOURCE_REFERENCE, contact_reference,
    map_summary, normalized_area, professional_reference_keys,
)


class DirectoryReferenceTests(unittest.TestCase):
    def test_exact_normalized_references_reject_conflicts_and_partial_names(self):
        reference, reason, field = contact_reference(" Puerto Plata ", "  SOsu\u0301a  ")
        self.assertEqual((reference["area_key"], reason, field), ("DO:sosua", None, "locality"))
        for province, locality, country, expected in (
            ("Santiago", "Sosúa", None, "area_conflict"),
            ("Puerto Plata", "Sosúa", "US", "country_conflict"),
            ("Puerto Plata resort", None, None, "unknown_area"),
            (None, "near Sosúa", None, "unknown_area"),
            (None, None, None, "no_location"),
        ):
            self.assertEqual(contact_reference(province, locality, country), (None, expected, None))
        reference, reason, field = contact_reference("Puerto Plata", "Uncatalogued quarter")
        self.assertEqual((reference["area_key"], reason, field), ("DO:puerto-plata", None, "province"))
        self.assertEqual(normalized_area(" Las\tTerrenas "), "las terrenas")

    def test_complete_catalog_has_explicit_approximate_sources_and_global_coordinate_bounds(self):
        self.assertEqual(len(AREAS), 38)
        self.assertEqual(len(AREA_KEYS), 38)
        self.assertEqual(AREAS["sosua"]["latitude"], 19.7517)
        self.assertEqual(AREAS["azua"]["area_kind"], "provincial_capital")
        for reference in AREAS.values():
            area = DirectoryMapArea(**reference, purpose="record_area", record_count=1)
            self.assertEqual(area.precision, "area_reference")
            self.assertEqual(area.source_reference, SOURCE_REFERENCE)
            self.assertNotIn("country_code", area.model_dump())
        # Bounds belong to the universal DTO, not just today's DO references.
        values = dict(AREAS["sosua"], purpose="record_area", record_count=1,
                      latitude=40.7128, longitude=-74.006, reference_country_code="US")
        self.assertEqual(DirectoryMapArea(**values).reference_country_code, "US")
        for key, invalid in (("latitude", 91), ("longitude", -181),
                             ("latitude", float("nan")), ("longitude", float("inf"))):
            with self.assertRaises(ValidationError):
                DirectoryMapArea(**{**values, key: invalid})

    def test_unique_service_area_counts_preserve_unmapped_and_selected_scope(self):
        keys = professional_reference_keys(["sosua", "sosua", "cabarete", "north_coast", "unknown"])
        self.assertEqual(keys, {"DO:sosua", "DO:cabarete"})
        self.assertEqual(professional_reference_keys(["sosua"], "US"), set())
        self.assertEqual(professional_reference_keys(["nationwide", "cabrera"]), set())
        self.assertEqual(professional_reference_keys(["sosua", "cabarete"], map_area="DO:sosua"), {"DO:sosua"})
        summary = DirectoryMapSummary(**map_summary({"DO:sosua": 2, "DO:cabarete": 1}, 3, 2, "service_area"))
        self.assertEqual((summary.total, summary.mapped_total, summary.unmapped_total), (3, 2, 1))
        self.assertEqual(sum(area.record_count for area in summary.areas), 3)
        self.assertEqual(len({area.area_key for area in summary.areas}), len(summary.areas))
        self.assertEqual(summary.catalog.model_dump(), CATALOG)
        self.assertTrue(summary.complete)
