"""Existing area references only: no geocoding, office inference or stored edits."""

import re
import unicodedata

from sqlalchemy import and_, case, func, or_


CATALOG_ID = "existing-dr-area-references-v1"
SOURCE_REFERENCE = "apps/landing/src/data/searchData.ts@7ec86e649fa1830fa159274759e22212035469bb"
CATALOG = {
    "id": CATALOG_ID,
    "reference_country_code": "DO",
    "source_reference": SOURCE_REFERENCE,
    "purpose": "approximate_area_references",
}
ACCENTED = "áéíóúüñàèìòùâêîôûäëïöÿçÁÉÍÓÚÜÑÀÈÌÒÙÂÊÎÔÛÄËÏÖŸÇ"
UNACCENTED = "aeiouunaeiouaeiouaeioycaeiouunaeiouaeiouaeioyc"
TRANSLATION = str.maketrans(ACCENTED, UNACCENTED)

# Copied unchanged from the existing frontend reference catalog. These are
# provincial capitals/popular localities, not geometric centroids or offices.
CAPITALS = {
    "Azua": (18.4531, -70.7350), "Bahoruco": (18.4833, -71.4167),
    "Barahona": (18.2085, -71.1008), "Dajabón": (19.5500, -71.7083),
    "Distrito Nacional": (18.4861, -69.9312), "Duarte": (19.3000, -70.2500),
    "El Seibo": (18.7667, -69.0389), "Elías Piña": (18.8783, -71.7000),
    "Espaillat": (19.3939, -70.5261), "Hato Mayor": (18.7625, -69.2564),
    "Hermanas Mirabal": (19.3806, -70.4183), "Independencia": (18.4922, -71.8508),
    "La Altagracia": (18.6157, -68.7080), "La Romana": (18.4273, -68.9728),
    "La Vega": (19.2214, -70.5292), "María Trinidad Sánchez": (19.3831, -69.8475),
    "Monseñor Nouel": (18.9367, -70.4092), "Monte Cristi": (19.8500, -71.6500),
    "Monte Plata": (18.8069, -69.7847), "Pedernales": (18.0383, -71.7442),
    "Peravia": (18.2797, -70.3308), "San Cristóbal": (18.4167, -70.1000),
    "San José de Ocoa": (18.5453, -70.5069), "San Juan": (18.8064, -71.2294),
    "San Pedro de Macorís": (18.4539, -69.3086), "Sánchez Ramírez": (19.0553, -70.1500),
    "Santiago Rodríguez": (19.4772, -71.3400), "Valverde": (19.5514, -71.0781),
}
LOCALITIES = {
    "Puerto Plata": (19.7947, -70.6870), "Sosúa": (19.7517, -70.5168),
    "Cabarete": (19.7669, -70.4101), "Samaná": (19.2063, -69.3362),
    "Las Terrenas": (19.3086, -69.5433), "Santiago": (19.4505, -70.6918),
    "Jarabacoa": (19.1199, -70.6400), "Santo Domingo": (18.4861, -69.9312),
    "Punta Cana": (18.5601, -68.3725), "Cap Cana": (18.4667, -68.3633),
}


def normalized_area(value):
    value = value if isinstance(value, str) else ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip().lower().translate(TRANSLATION)


def normalized_column(column):
    compact = func.btrim(func.regexp_replace(func.coalesce(column, ""), r"\s+", " ", "g"))
    return func.translate(func.lower(func.normalize(compact)), ACCENTED, UNACCENTED)


AREAS = {}
for values, kind in ((CAPITALS, "provincial_capital"), (LOCALITIES, "popular_locality")):
    for name, (latitude, longitude) in values.items():
        normalized = normalized_area(name)
        AREAS[normalized] = {
            "area_key": "DO:" + normalized.replace(" ", "-"),
            "label": name,
            "latitude": latitude,
            "longitude": longitude,
            "reference_country_code": "DO",
            "precision": "area_reference",
            "source_kind": "existing_area_catalog",
            "source_reference": SOURCE_REFERENCE,
            "area_kind": kind,
        }
AREA_KEYS = frozenset(area["area_key"] for area in AREAS.values())
PROVINCES = frozenset(normalized_area(name) for name in (*CAPITALS, "Puerto Plata", "Samaná", "Santiago", "Santo Domingo"))
LOCALITY_PROVINCES = {key: {key} for key in AREAS}
for town, provinces in {
    "Sosúa": ("Puerto Plata",), "Cabarete": ("Puerto Plata",), "Las Terrenas": ("Samaná",),
    "Jarabacoa": ("La Vega",), "Punta Cana": ("La Altagracia",), "Cap Cana": ("La Altagracia",),
    "Santo Domingo": ("Santo Domingo", "Distrito Nacional"),
}.items():
    LOCALITY_PROVINCES[normalized_area(town)] = {normalized_area(name) for name in provinces}
PROFESSIONAL_AREAS = {identifier: normalized_area(name) for identifier, name in {
    "puerto_plata": "Puerto Plata", "sosua": "Sosúa", "cabarete": "Cabarete", "samana": "Samaná",
    "las_terrenas": "Las Terrenas", "punta_cana": "Punta Cana", "cap_cana": "Cap Cana", "la_romana": "La Romana",
    "santo_domingo": "Santo Domingo", "santiago": "Santiago", "jarabacoa": "Jarabacoa",
    "barahona": "Barahona", "pedernales": "Pedernales",
}.items()}


def country_text(value):
    if value is None:
        return ""
    return value.strip().upper() if isinstance(value, str) else "!"


def contact_reference(province, locality, country=None):
    country_value = country_text(country)
    if country_value and country_value != "DO":
        return None, "country_conflict", None
    town, region = normalized_area(locality), normalized_area(province)
    if town in AREAS:
        if region and region not in LOCALITY_PROVINCES[town]:
            return None, "area_conflict", None
        return AREAS[town], None, "locality"
    if region in PROVINCES:
        return AREAS[region], None, "province"
    return None, "unknown_area" if town or region else "no_location", None


def contact_reference_expression(province, locality, country):
    town, region = normalized_column(locality), normalized_column(province)
    allowed_country = func.upper(func.btrim(func.coalesce(country, ""))).in_(("", "DO"))
    choices = [
        (
            and_(allowed_country, town == key,
                 or_(region == "", region.in_(tuple(LOCALITY_PROVINCES[key])))),
            area["area_key"],
        )
        for key, area in AREAS.items()
    ]
    choices.extend(
        (and_(allowed_country, town.not_in(tuple(AREAS)), region == key), AREAS[key]["area_key"])
        for key in sorted(PROVINCES)
    )
    return case(*choices, else_=None)


def professional_mapped_expression(published, map_area=""):
    identifiers = [
        identifier for identifier, area in PROFESSIONAL_AREAS.items()
        if not map_area or AREAS[area]["area_key"] == map_area
    ]
    covered = or_(*(published["service_area_ids"].contains([identifier]) for identifier in identifiers))
    allowed_country = func.upper(func.btrim(func.coalesce(published["country_code"].astext, ""))).in_(("", "DO"))
    return and_(allowed_country, func.coalesce(covered, False))


def professional_reference_keys(identifiers, country=None, map_area=""):
    if country_text(country) not in {"", "DO"} or not isinstance(identifiers, list):
        return set()
    return {
        AREAS[PROFESSIONAL_AREAS[identifier]]["area_key"]
        for identifier in identifiers
        if isinstance(identifier, str) and identifier in PROFESSIONAL_AREAS
        and (not map_area or AREAS[PROFESSIONAL_AREAS[identifier]]["area_key"] == map_area)
    }


def map_summary(counts, total, mapped_total, purpose):
    references = {area["area_key"]: area for area in AREAS.values()}
    return {
        "total": total,
        "mapped_total": mapped_total,
        "unmapped_total": total - mapped_total,
        "complete": True,
        "catalog": CATALOG,
        "areas": [
            dict(references[key], record_count=count, purpose=purpose)
            for key, count in sorted(counts.items()) if count
        ],
    }
