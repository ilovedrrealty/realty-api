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
    # Explicit administrative coverage is separate from the legacy towns/regions.
    # These choices do not infer town membership, map points or regional coverage.
    option("province_distrito_nacional", "National District (Distrito Nacional)", "Distrito Nacional"),
    option("province_azua", "Azua Province", "Provincia de Azua"),
    option("province_baoruco", "Baoruco Province", "Provincia de Baoruco"),
    option("province_barahona", "Barahona Province", "Provincia de Barahona"),
    option("province_dajabon", "Dajabón Province", "Provincia de Dajabón"),
    option("province_duarte", "Duarte Province", "Provincia de Duarte"),
    option("province_elias_pina", "Elías Piña Province", "Provincia de Elías Piña"),
    option("province_el_seibo", "El Seibo Province", "Provincia de El Seibo"),
    option("province_espaillat", "Espaillat Province", "Provincia de Espaillat"),
    option("province_independencia", "Independencia Province", "Provincia de Independencia"),
    option("province_la_altagracia", "La Altagracia Province", "Provincia de La Altagracia"),
    option("province_la_romana", "La Romana Province", "Provincia de La Romana"),
    option("province_la_vega", "La Vega Province", "Provincia de La Vega"),
    option("province_maria_trinidad_sanchez", "María Trinidad Sánchez Province", "Provincia de María Trinidad Sánchez"),
    option("province_monte_cristi", "Monte Cristi Province", "Provincia de Monte Cristi"),
    option("province_pedernales", "Pedernales Province", "Provincia de Pedernales"),
    option("province_peravia", "Peravia Province", "Provincia de Peravia"),
    option("province_puerto_plata", "Puerto Plata Province", "Provincia de Puerto Plata"),
    option("province_hermanas_mirabal", "Hermanas Mirabal Province", "Provincia de Hermanas Mirabal"),
    option("province_samana", "Samaná Province", "Provincia de Samaná"),
    option("province_san_cristobal", "San Cristóbal Province", "Provincia de San Cristóbal"),
    option("province_san_juan", "San Juan Province", "Provincia de San Juan"),
    option("province_san_pedro_de_macoris", "San Pedro de Macorís Province", "Provincia de San Pedro de Macorís"),
    option("province_sanchez_ramirez", "Sánchez Ramírez Province", "Provincia de Sánchez Ramírez"),
    option("province_santiago", "Santiago Province", "Provincia de Santiago"),
    option("province_santiago_rodriguez", "Santiago Rodríguez Province", "Provincia de Santiago Rodríguez"),
    option("province_valverde", "Valverde Province", "Provincia de Valverde"),
    option("province_monsenor_nouel", "Monseñor Nouel Province", "Provincia de Monseñor Nouel"),
    option("province_monte_plata", "Monte Plata Province", "Provincia de Monte Plata"),
    option("province_hato_mayor", "Hato Mayor Province", "Provincia de Hato Mayor"),
    option("province_san_jose_de_ocoa", "San José de Ocoa Province", "Provincia de San José de Ocoa"),
    option("province_santo_domingo", "Santo Domingo Province", "Provincia de Santo Domingo"),
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
