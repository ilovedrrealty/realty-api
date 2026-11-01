"""Bounded service taxonomy; membership never changes an account's auth role."""


def option(identifier, en, es):
    return {"id": identifier, "label": {"en": en, "es": es}}


CATEGORIES = [
    option("real_estate_agent", "Real estate agents and brokerages", "Agentes y empresas inmobiliarias"),
    option("property_law", "Property lawyers", "Abogados inmobiliarios"),
    option("notary", "Notaries", "Notarios"),
    option("surveying", "Land surveyors", "Agrimensores"),
    option("mortgage_lending", "Mortgage lenders", "Financiamiento hipotecario"),
    option("banking", "Banking services", "Servicios bancarios"),
    option("insurance", "Insurance", "Seguros"),
    option("accounting_tax", "Accounting and taxes", "Contabilidad e impuestos"),
    option("architecture", "Architects", "Arquitectos"),
    option("engineering", "Engineers", "Ingenieros"),
    option("construction", "Construction", "Construcción"),
    option("renovation", "Renovations", "Remodelaciones"),
    option("electrical", "Electricians", "Electricistas"),
    option("plumbing", "Plumbers", "Plomeros"),
    option("air_conditioning", "Air conditioning", "Aire acondicionado"),
    option("interior_design", "Interior designers", "Diseñadores de interiores"),
    option("property_management", "Property management", "Administración de propiedades"),
    option("vacation_rental_management", "Vacation rental management", "Administración de alquileres vacacionales"),
    option("cleaning", "Cleaning", "Limpieza"),
    option("landscaping", "Landscaping", "Jardinería y paisajismo"),
    option("pool_service", "Pool maintenance", "Mantenimiento de piscinas"),
    option("security", "Security", "Seguridad"),
    option("moving_storage", "Moving and storage", "Mudanzas y almacenamiento"),
    option("home_inspection", "Property inspections", "Inspección de propiedades"),
    option("solar_energy", "Solar energy", "Energía solar"),
    option("internet_utilities", "Internet and utilities", "Internet y servicios básicos"),
    option("other_services", "Other property services", "Otros servicios inmobiliarios"),
    option("handyman", "Handyman and general repairs", "Mantenimiento general y reparaciones"),
    option("painting", "Painting", "Pintura"),
    option("roofing_waterproofing", "Roofing and waterproofing", "Techos e impermeabilización"),
    option("pest_control", "Pest control", "Control de plagas"),
    option("appliance_repair", "Appliance repair", "Reparación de electrodomésticos"),
    option("carpentry_cabinetry", "Carpentry and cabinetry", "Carpintería y ebanistería"),
    option("metalwork_welding", "Metalwork and welding", "Herrería y soldadura"),
    option("flooring_tiling", "Flooring and tile", "Pisos y revestimientos"),
    option("water_systems", "Pumps, cisterns and water treatment", "Bombas, cisternas y tratamiento de agua"),
    option("septic_drainage", "Septic systems and drainage", "Sistemas sépticos y drenaje"),
    option("backup_power", "Inverters, batteries and generators", "Inversores, baterías y generadores"),
    option("smart_property", "Access control and property technology", "Control de acceso y tecnología para inmuebles"),
    option("fire_safety", "Fire protection and safety equipment", "Protección contra incendios y equipos de seguridad"),
    option("waste_removal", "Waste and construction-debris removal", "Retiro de residuos y escombros"),
    option("property_media", "Property photography, video and staging", "Fotografía, video y preparación de inmuebles"),
    option("agricultural_services", "Agricultural property services", "Servicios para propiedades agropecuarias"),
]
# Network context only: these options do not extend Listing.type or change auth roles.
PROPERTY_CLASSES = [
    option("house_villa", "House or villa", "Casa o villa"),
    option("apartment_condominium", "Apartment or condominium", "Apartamento o condominio"),
    option("multiunit_building", "Multi-unit building or association", "Edificio de varias unidades o asociación"),
    option("vacation_rental", "Vacation rental", "Alquiler vacacional"),
    option("hotel_hospitality", "Hotel, guesthouse or resort", "Hotel, casa de huéspedes o complejo turístico"),
    option("office", "Office", "Oficina"),
    option("retail_restaurant", "Retail space or restaurant", "Local comercial o restaurante"),
    option("warehouse_industrial", "Warehouse or industrial property", "Almacén o inmueble industrial"),
    option("farm_rural", "Farm or rural property", "Finca o propiedad rural"),
    option("vacant_land", "Vacant development land", "Terreno sin desarrollar"),
]
AREAS = [
    option("nationwide", "Dominican Republic — nationwide", "República Dominicana — todo el país"),
    option("north_coast", "North Coast", "Costa Norte"),
    option("puerto_plata", "Puerto Plata", "Puerto Plata"),
    option("sosua", "Sosúa", "Sosúa"),
    option("cabarete", "Cabarete", "Cabarete"),
    option("cabrera", "Cabrera", "Cabrera"),
    option("samana", "Samaná Peninsula", "Península de Samaná"),
    option("las_terrenas", "Las Terrenas", "Las Terrenas"),
    option("east", "East Coast", "Región Este"),
    option("punta_cana", "Punta Cana", "Punta Cana"),
    option("cap_cana", "Cap Cana", "Cap Cana"),
    option("la_romana", "La Romana", "La Romana"),
    option("santo_domingo", "Santo Domingo", "Santo Domingo"),
    option("central", "Central and Cibao", "Región Central y Cibao"),
    option("santiago", "Santiago", "Santiago"),
    option("jarabacoa", "Jarabacoa", "Jarabacoa"),
    option("south", "South and Southwest", "Sur y Suroeste"),
    option("barahona", "Barahona", "Barahona"),
    option("pedernales", "Pedernales", "Pedernales"),
]
LANGUAGES = [option("es", "Spanish", "Español"), option("en", "English", "Inglés"),
             option("fr", "French", "Francés"), option("de", "German", "Alemán"),
             option("it", "Italian", "Italiano"), option("pt", "Portuguese", "Portugués"),
             option("ru", "Russian", "Ruso"), option("other", "Other", "Otro")]
CATEGORY_IDS = frozenset(item["id"] for item in CATEGORIES)
PROPERTY_CLASS_IDS = frozenset(item["id"] for item in PROPERTY_CLASSES)
AREA_IDS = frozenset(item["id"] for item in AREAS)
LANGUAGE_IDS = frozenset(item["id"] for item in LANGUAGES)
AREA_CHILDREN = {"north_coast": {"puerto_plata", "sosua", "cabarete", "cabrera"},
                 "samana": {"las_terrenas"}, "east": {"punta_cana", "cap_cana", "la_romana"},
                 "central": {"santiago", "jarabacoa"}, "south": {"barahona", "pedernales"}}


def matching_areas(identifier):
    """Include declared regional/national coverage when visitors select a town."""
    if identifier == "nationwide":
        return {"nationwide"}
    matches = {identifier, "nationwide"} | AREA_CHILDREN.get(identifier, set())
    matches.update(parent for parent, children in AREA_CHILDREN.items() if identifier in children)
    return matches
