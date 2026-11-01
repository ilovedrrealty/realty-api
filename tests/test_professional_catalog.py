"""Pure catalog and options-response contracts; no database or provider imports."""
import ast
from pathlib import Path
import unittest

from utils.professional_options import (
    CATEGORIES, CATEGORY_IDS, PROPERTY_CLASSES, PROPERTY_CLASS_IDS,
    AREAS, AREA_IDS, LANGUAGES, LANGUAGE_IDS, matching_areas,
)


# Published option identities and labels are a compatibility contract for saved drafts.
LEGACY_CATEGORIES = (
    ("real_estate_agent", "Real estate agents and brokerages", "Agentes y empresas inmobiliarias"),
    ("property_law", "Property lawyers", "Abogados inmobiliarios"),
    ("notary", "Notaries", "Notarios"),
    ("surveying", "Land surveyors", "Agrimensores"),
    ("mortgage_lending", "Mortgage lenders", "Financiamiento hipotecario"),
    ("banking", "Banking services", "Servicios bancarios"),
    ("insurance", "Insurance", "Seguros"),
    ("accounting_tax", "Accounting and taxes", "Contabilidad e impuestos"),
    ("architecture", "Architects", "Arquitectos"),
    ("engineering", "Engineers", "Ingenieros"),
    ("construction", "Construction", "Construcción"),
    ("renovation", "Renovations", "Remodelaciones"),
    ("electrical", "Electricians", "Electricistas"),
    ("plumbing", "Plumbers", "Plomeros"),
    ("air_conditioning", "Air conditioning", "Aire acondicionado"),
    ("interior_design", "Interior designers", "Diseñadores de interiores"),
    ("property_management", "Property management", "Administración de propiedades"),
    ("vacation_rental_management", "Vacation rental management", "Administración de alquileres vacacionales"),
    ("cleaning", "Cleaning", "Limpieza"),
    ("landscaping", "Landscaping", "Jardinería y paisajismo"),
    ("pool_service", "Pool maintenance", "Mantenimiento de piscinas"),
    ("security", "Security", "Seguridad"),
    ("moving_storage", "Moving and storage", "Mudanzas y almacenamiento"),
    ("home_inspection", "Property inspections", "Inspección de propiedades"),
    ("solar_energy", "Solar energy", "Energía solar"),
    ("internet_utilities", "Internet and utilities", "Internet y servicios básicos"),
    ("other_services", "Other property services", "Otros servicios inmobiliarios"),
)
NEW_SERVICE_IDS = {
    "handyman", "painting", "roofing_waterproofing", "pest_control", "appliance_repair",
    "carpentry_cabinetry", "metalwork_welding", "flooring_tiling", "water_systems",
    "septic_drainage", "backup_power", "smart_property", "fire_safety", "waste_removal",
    "property_media", "agricultural_services",
}
PROPERTY_CONTEXT_IDS = {
    "house_villa", "apartment_condominium", "multiunit_building", "vacation_rental",
    "hotel_hospitality", "office", "retail_restaurant", "warehouse_industrial",
    "farm_rural", "vacant_land",
}


class ProfessionalCatalogTests(unittest.TestCase):
    def test_existing_category_ids_labels_and_order_are_preserved(self):
        observed = tuple((item["id"], item["label"]["en"], item["label"]["es"])
                         for item in CATEGORIES[:len(LEGACY_CATEGORIES)])
        self.assertEqual(observed, LEGACY_CATEGORIES)

    def test_reviewed_service_expansion_is_additive(self):
        legacy_ids = {item[0] for item in LEGACY_CATEGORIES}
        self.assertEqual(CATEGORY_IDS - legacy_ids, NEW_SERVICE_IDS)
        self.assertEqual(len(CATEGORIES), 43)
        self.assertEqual(len(CATEGORIES), len(CATEGORY_IDS))

    def test_all_options_have_unique_ids_and_nonempty_english_spanish_labels(self):
        for catalog, identifiers in ((CATEGORIES, CATEGORY_IDS), (PROPERTY_CLASSES, PROPERTY_CLASS_IDS),
                                     (AREAS, AREA_IDS), (LANGUAGES, LANGUAGE_IDS)):
            with self.subTest(catalog=catalog[0]["id"]):
                self.assertIsInstance(identifiers, frozenset)
                self.assertEqual(len(catalog), len(identifiers))
                self.assertEqual({item["id"] for item in catalog}, identifiers)
                for item in catalog:
                    self.assertEqual(set(item), {"id", "label"})
                    self.assertRegex(item["id"], r"^[a-z][a-z0-9_]*$")
                    self.assertEqual(set(item["label"]), {"en", "es"})
                    for label in item["label"].values():
                        self.assertIsInstance(label, str)
                        self.assertTrue(label.strip())
                        self.assertEqual(label, label.strip())
                        self.assertFalse(any(ord(char) < 32 for char in label))

    def test_property_classes_are_separate_from_professional_service_categories(self):
        self.assertEqual(PROPERTY_CLASS_IDS, PROPERTY_CONTEXT_IDS)
        self.assertTrue(PROPERTY_CLASS_IDS.isdisjoint(CATEGORY_IDS))
        self.assertTrue(PROPERTY_CLASS_IDS.isdisjoint({"buyer", "owner", "realtor", "admin"}))
        # A property class is contextual metadata, not a substitute service option.
        self.assertNotIn("hotel_hospitality", CATEGORY_IDS)
        self.assertIn("property_management", CATEGORY_IDS)

    def test_existing_geographic_matching_is_unchanged(self):
        self.assertEqual(len(AREAS), 19)
        self.assertEqual(matching_areas("nationwide"), {"nationwide"})
        self.assertEqual(matching_areas("cabarete"), {"cabarete", "north_coast", "nationwide"})
        self.assertEqual(matching_areas("north_coast"),
                         {"north_coast", "puerto_plata", "sosua", "cabarete", "cabrera", "nationwide"})
        self.assertNotIn("punta_cana", matching_areas("cabarete"))

    def test_language_catalog_retains_existing_choices(self):
        self.assertEqual(LANGUAGE_IDS, {"es", "en", "fr", "de", "it", "pt", "ru", "other"})

    def test_options_consumer_retains_existing_keys_and_exposes_property_classes(self):
        # Execute only this actual pure route body, without importing FastAPI/DB routes.
        source = Path(__file__).resolve().parents[1] / "routes" / "professionals.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == "professional_options")
        function.decorator_list = []
        namespace = {"CATEGORIES": CATEGORIES, "PROPERTY_CLASSES": PROPERTY_CLASSES,
                     "AREAS": AREAS, "LANGUAGES": LANGUAGES}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        response = namespace["professional_options"]()
        self.assertEqual(set(response), {"categories", "areas", "languages", "property_classes"})
        self.assertEqual(response["categories"], CATEGORIES)
        self.assertEqual(response["areas"], AREAS)
        self.assertEqual(response["languages"], LANGUAGES)
        self.assertEqual(response["property_classes"], PROPERTY_CLASSES)


if __name__ == "__main__":
    unittest.main()
