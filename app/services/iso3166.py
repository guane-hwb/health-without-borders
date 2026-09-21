"""
ISO 3166-1 country code conversion.

The clinical app stores nationality and country of residence as ISO 3166-1
*alpha-3* codes ('COL', 'VEN'). The RDA implementation guide binds both
``ExtensionPatientNationality`` and ``ExtensionCountryCode`` to a value set of
ISO 3166-1 *numeric* codes ('170', '862'), with a ``required`` strength. This
module converts between the two so the mismatch is resolved exactly once, at
the FHIR boundary, without touching the internal model or the NFC payloads
already written to patient wristbands.

The table below is frozen data generated from the ISO 3166-1 standard (all 249
officially assigned entries). It is deliberately checked in rather than pulled
from a runtime dependency: country codes change on the order of once a decade,
and a terminology mapping that silently shifts under a transitive dependency
upgrade is not something a clinical bundle builder should have.

``display`` values are the ISO short names. FHIR treats ``Coding.display`` as
advisory rather than as part of the code's identity, so a mismatch against the
guide's own display strings is at worst a validator warning.
"""

from typing import Optional

# (alpha-2, alpha-3, numeric, ISO short name), ordered by numeric code.
_COUNTRIES: tuple[tuple[str, str, str, str], ...] = (
    ("AF", "AFG", "004", "Afghanistan"),
    ("AL", "ALB", "008", "Albania"),
    ("AQ", "ATA", "010", "Antarctica"),
    ("DZ", "DZA", "012", "Algeria"),
    ("AS", "ASM", "016", "American Samoa"),
    ("AD", "AND", "020", "Andorra"),
    ("AO", "AGO", "024", "Angola"),
    ("AG", "ATG", "028", "Antigua and Barbuda"),
    ("AZ", "AZE", "031", "Azerbaijan"),
    ("AR", "ARG", "032", "Argentina"),
    ("AU", "AUS", "036", "Australia"),
    ("AT", "AUT", "040", "Austria"),
    ("BS", "BHS", "044", "Bahamas"),
    ("BH", "BHR", "048", "Bahrain"),
    ("BD", "BGD", "050", "Bangladesh"),
    ("AM", "ARM", "051", "Armenia"),
    ("BB", "BRB", "052", "Barbados"),
    ("BE", "BEL", "056", "Belgium"),
    ("BM", "BMU", "060", "Bermuda"),
    ("BT", "BTN", "064", "Bhutan"),
    ("BO", "BOL", "068", "Bolivia, Plurinational State of"),
    ("BA", "BIH", "070", "Bosnia and Herzegovina"),
    ("BW", "BWA", "072", "Botswana"),
    ("BV", "BVT", "074", "Bouvet Island"),
    ("BR", "BRA", "076", "Brazil"),
    ("BZ", "BLZ", "084", "Belize"),
    ("IO", "IOT", "086", "British Indian Ocean Territory"),
    ("SB", "SLB", "090", "Solomon Islands"),
    ("VG", "VGB", "092", "Virgin Islands, British"),
    ("BN", "BRN", "096", "Brunei Darussalam"),
    ("BG", "BGR", "100", "Bulgaria"),
    ("MM", "MMR", "104", "Myanmar"),
    ("BI", "BDI", "108", "Burundi"),
    ("BY", "BLR", "112", "Belarus"),
    ("KH", "KHM", "116", "Cambodia"),
    ("CM", "CMR", "120", "Cameroon"),
    ("CA", "CAN", "124", "Canada"),
    ("CV", "CPV", "132", "Cabo Verde"),
    ("KY", "CYM", "136", "Cayman Islands"),
    ("CF", "CAF", "140", "Central African Republic"),
    ("LK", "LKA", "144", "Sri Lanka"),
    ("TD", "TCD", "148", "Chad"),
    ("CL", "CHL", "152", "Chile"),
    ("CN", "CHN", "156", "China"),
    ("TW", "TWN", "158", "Taiwan, Province of China"),
    ("CX", "CXR", "162", "Christmas Island"),
    ("CC", "CCK", "166", "Cocos (Keeling) Islands"),
    ("CO", "COL", "170", "Colombia"),
    ("KM", "COM", "174", "Comoros"),
    ("YT", "MYT", "175", "Mayotte"),
    ("CG", "COG", "178", "Congo"),
    ("CD", "COD", "180", "Congo, The Democratic Republic of the"),
    ("CK", "COK", "184", "Cook Islands"),
    ("CR", "CRI", "188", "Costa Rica"),
    ("HR", "HRV", "191", "Croatia"),
    ("CU", "CUB", "192", "Cuba"),
    ("CY", "CYP", "196", "Cyprus"),
    ("CZ", "CZE", "203", "Czechia"),
    ("BJ", "BEN", "204", "Benin"),
    ("DK", "DNK", "208", "Denmark"),
    ("DM", "DMA", "212", "Dominica"),
    ("DO", "DOM", "214", "Dominican Republic"),
    ("EC", "ECU", "218", "Ecuador"),
    ("SV", "SLV", "222", "El Salvador"),
    ("GQ", "GNQ", "226", "Equatorial Guinea"),
    ("ET", "ETH", "231", "Ethiopia"),
    ("ER", "ERI", "232", "Eritrea"),
    ("EE", "EST", "233", "Estonia"),
    ("FO", "FRO", "234", "Faroe Islands"),
    ("FK", "FLK", "238", "Falkland Islands (Malvinas)"),
    ("GS", "SGS", "239", "South Georgia and the South Sandwich Islands"),
    ("FJ", "FJI", "242", "Fiji"),
    ("FI", "FIN", "246", "Finland"),
    ("AX", "ALA", "248", "Åland Islands"),
    ("FR", "FRA", "250", "France"),
    ("GF", "GUF", "254", "French Guiana"),
    ("PF", "PYF", "258", "French Polynesia"),
    ("TF", "ATF", "260", "French Southern Territories"),
    ("DJ", "DJI", "262", "Djibouti"),
    ("GA", "GAB", "266", "Gabon"),
    ("GE", "GEO", "268", "Georgia"),
    ("GM", "GMB", "270", "Gambia"),
    ("PS", "PSE", "275", "Palestine, State of"),
    ("DE", "DEU", "276", "Germany"),
    ("GH", "GHA", "288", "Ghana"),
    ("GI", "GIB", "292", "Gibraltar"),
    ("KI", "KIR", "296", "Kiribati"),
    ("GR", "GRC", "300", "Greece"),
    ("GL", "GRL", "304", "Greenland"),
    ("GD", "GRD", "308", "Grenada"),
    ("GP", "GLP", "312", "Guadeloupe"),
    ("GU", "GUM", "316", "Guam"),
    ("GT", "GTM", "320", "Guatemala"),
    ("GN", "GIN", "324", "Guinea"),
    ("GY", "GUY", "328", "Guyana"),
    ("HT", "HTI", "332", "Haiti"),
    ("HM", "HMD", "334", "Heard Island and McDonald Islands"),
    ("VA", "VAT", "336", "Holy See (Vatican City State)"),
    ("HN", "HND", "340", "Honduras"),
    ("HK", "HKG", "344", "Hong Kong"),
    ("HU", "HUN", "348", "Hungary"),
    ("IS", "ISL", "352", "Iceland"),
    ("IN", "IND", "356", "India"),
    ("ID", "IDN", "360", "Indonesia"),
    ("IR", "IRN", "364", "Iran, Islamic Republic of"),
    ("IQ", "IRQ", "368", "Iraq"),
    ("IE", "IRL", "372", "Ireland"),
    ("IL", "ISR", "376", "Israel"),
    ("IT", "ITA", "380", "Italy"),
    ("CI", "CIV", "384", "Côte d'Ivoire"),
    ("JM", "JAM", "388", "Jamaica"),
    ("JP", "JPN", "392", "Japan"),
    ("KZ", "KAZ", "398", "Kazakhstan"),
    ("JO", "JOR", "400", "Jordan"),
    ("KE", "KEN", "404", "Kenya"),
    ("KP", "PRK", "408", "Korea, Democratic People's Republic of"),
    ("KR", "KOR", "410", "Korea, Republic of"),
    ("KW", "KWT", "414", "Kuwait"),
    ("KG", "KGZ", "417", "Kyrgyzstan"),
    ("LA", "LAO", "418", "Lao People's Democratic Republic"),
    ("LB", "LBN", "422", "Lebanon"),
    ("LS", "LSO", "426", "Lesotho"),
    ("LV", "LVA", "428", "Latvia"),
    ("LR", "LBR", "430", "Liberia"),
    ("LY", "LBY", "434", "Libya"),
    ("LI", "LIE", "438", "Liechtenstein"),
    ("LT", "LTU", "440", "Lithuania"),
    ("LU", "LUX", "442", "Luxembourg"),
    ("MO", "MAC", "446", "Macao"),
    ("MG", "MDG", "450", "Madagascar"),
    ("MW", "MWI", "454", "Malawi"),
    ("MY", "MYS", "458", "Malaysia"),
    ("MV", "MDV", "462", "Maldives"),
    ("ML", "MLI", "466", "Mali"),
    ("MT", "MLT", "470", "Malta"),
    ("MQ", "MTQ", "474", "Martinique"),
    ("MR", "MRT", "478", "Mauritania"),
    ("MU", "MUS", "480", "Mauritius"),
    ("MX", "MEX", "484", "Mexico"),
    ("MC", "MCO", "492", "Monaco"),
    ("MN", "MNG", "496", "Mongolia"),
    ("MD", "MDA", "498", "Moldova, Republic of"),
    ("ME", "MNE", "499", "Montenegro"),
    ("MS", "MSR", "500", "Montserrat"),
    ("MA", "MAR", "504", "Morocco"),
    ("MZ", "MOZ", "508", "Mozambique"),
    ("OM", "OMN", "512", "Oman"),
    ("NA", "NAM", "516", "Namibia"),
    ("NR", "NRU", "520", "Nauru"),
    ("NP", "NPL", "524", "Nepal"),
    ("NL", "NLD", "528", "Netherlands"),
    ("CW", "CUW", "531", "Curaçao"),
    ("AW", "ABW", "533", "Aruba"),
    ("SX", "SXM", "534", "Sint Maarten (Dutch part)"),
    ("BQ", "BES", "535", "Bonaire, Sint Eustatius and Saba"),
    ("NC", "NCL", "540", "New Caledonia"),
    ("VU", "VUT", "548", "Vanuatu"),
    ("NZ", "NZL", "554", "New Zealand"),
    ("NI", "NIC", "558", "Nicaragua"),
    ("NE", "NER", "562", "Niger"),
    ("NG", "NGA", "566", "Nigeria"),
    ("NU", "NIU", "570", "Niue"),
    ("NF", "NFK", "574", "Norfolk Island"),
    ("NO", "NOR", "578", "Norway"),
    ("MP", "MNP", "580", "Northern Mariana Islands"),
    ("UM", "UMI", "581", "United States Minor Outlying Islands"),
    ("FM", "FSM", "583", "Micronesia, Federated States of"),
    ("MH", "MHL", "584", "Marshall Islands"),
    ("PW", "PLW", "585", "Palau"),
    ("PK", "PAK", "586", "Pakistan"),
    ("PA", "PAN", "591", "Panama"),
    ("PG", "PNG", "598", "Papua New Guinea"),
    ("PY", "PRY", "600", "Paraguay"),
    ("PE", "PER", "604", "Peru"),
    ("PH", "PHL", "608", "Philippines"),
    ("PN", "PCN", "612", "Pitcairn"),
    ("PL", "POL", "616", "Poland"),
    ("PT", "PRT", "620", "Portugal"),
    ("GW", "GNB", "624", "Guinea-Bissau"),
    ("TL", "TLS", "626", "Timor-Leste"),
    ("PR", "PRI", "630", "Puerto Rico"),
    ("QA", "QAT", "634", "Qatar"),
    ("RE", "REU", "638", "Réunion"),
    ("RO", "ROU", "642", "Romania"),
    ("RU", "RUS", "643", "Russian Federation"),
    ("RW", "RWA", "646", "Rwanda"),
    ("BL", "BLM", "652", "Saint Barthélemy"),
    ("SH", "SHN", "654", "Saint Helena, Ascension and Tristan da Cunha"),
    ("KN", "KNA", "659", "Saint Kitts and Nevis"),
    ("AI", "AIA", "660", "Anguilla"),
    ("LC", "LCA", "662", "Saint Lucia"),
    ("MF", "MAF", "663", "Saint Martin (French part)"),
    ("PM", "SPM", "666", "Saint Pierre and Miquelon"),
    ("VC", "VCT", "670", "Saint Vincent and the Grenadines"),
    ("SM", "SMR", "674", "San Marino"),
    ("ST", "STP", "678", "Sao Tome and Principe"),
    ("SA", "SAU", "682", "Saudi Arabia"),
    ("SN", "SEN", "686", "Senegal"),
    ("RS", "SRB", "688", "Serbia"),
    ("SC", "SYC", "690", "Seychelles"),
    ("SL", "SLE", "694", "Sierra Leone"),
    ("SG", "SGP", "702", "Singapore"),
    ("SK", "SVK", "703", "Slovakia"),
    ("VN", "VNM", "704", "Viet Nam"),
    ("SI", "SVN", "705", "Slovenia"),
    ("SO", "SOM", "706", "Somalia"),
    ("ZA", "ZAF", "710", "South Africa"),
    ("ZW", "ZWE", "716", "Zimbabwe"),
    ("ES", "ESP", "724", "Spain"),
    ("SS", "SSD", "728", "South Sudan"),
    ("SD", "SDN", "729", "Sudan"),
    ("EH", "ESH", "732", "Western Sahara"),
    ("SR", "SUR", "740", "Suriname"),
    ("SJ", "SJM", "744", "Svalbard and Jan Mayen"),
    ("SZ", "SWZ", "748", "Eswatini"),
    ("SE", "SWE", "752", "Sweden"),
    ("CH", "CHE", "756", "Switzerland"),
    ("SY", "SYR", "760", "Syrian Arab Republic"),
    ("TJ", "TJK", "762", "Tajikistan"),
    ("TH", "THA", "764", "Thailand"),
    ("TG", "TGO", "768", "Togo"),
    ("TK", "TKL", "772", "Tokelau"),
    ("TO", "TON", "776", "Tonga"),
    ("TT", "TTO", "780", "Trinidad and Tobago"),
    ("AE", "ARE", "784", "United Arab Emirates"),
    ("TN", "TUN", "788", "Tunisia"),
    ("TR", "TUR", "792", "Türkiye"),
    ("TM", "TKM", "795", "Turkmenistan"),
    ("TC", "TCA", "796", "Turks and Caicos Islands"),
    ("TV", "TUV", "798", "Tuvalu"),
    ("UG", "UGA", "800", "Uganda"),
    ("UA", "UKR", "804", "Ukraine"),
    ("MK", "MKD", "807", "North Macedonia"),
    ("EG", "EGY", "818", "Egypt"),
    ("GB", "GBR", "826", "United Kingdom"),
    ("GG", "GGY", "831", "Guernsey"),
    ("JE", "JEY", "832", "Jersey"),
    ("IM", "IMN", "833", "Isle of Man"),
    ("TZ", "TZA", "834", "Tanzania, United Republic of"),
    ("US", "USA", "840", "United States"),
    ("VI", "VIR", "850", "Virgin Islands, U.S."),
    ("BF", "BFA", "854", "Burkina Faso"),
    ("UY", "URY", "858", "Uruguay"),
    ("UZ", "UZB", "860", "Uzbekistan"),
    ("VE", "VEN", "862", "Venezuela, Bolivarian Republic of"),
    ("WF", "WLF", "876", "Wallis and Futuna"),
    ("WS", "WSM", "882", "Samoa"),
    ("YE", "YEM", "887", "Yemen"),
    ("ZM", "ZMB", "894", "Zambia"),
)

_ALPHA2_TO_NUMERIC = {alpha2: numeric for alpha2, _, numeric, _ in _COUNTRIES}
_ALPHA3_TO_NUMERIC = {alpha3: numeric for _, alpha3, numeric, _ in _COUNTRIES}
_NUMERIC_TO_DISPLAY = {numeric: name for _, _, numeric, name in _COUNTRIES}


def to_numeric(code: Optional[str]) -> Optional[str]:
    """
    Normalise any ISO 3166-1 representation to its numeric code.

    Accepts alpha-2 ('CO'), alpha-3 ('COL') and numeric ('170', '4') input,
    case-insensitively and with surrounding whitespace ignored. Numeric input
    is zero-padded to three digits and validated, so a code that is already in
    the target representation passes through untouched — which means migrating
    the stored representation later would require no change here.

    Returns ``None`` for anything unrecognised. Callers decide what an
    unmappable country means for them; this module does not guess.
    """
    if code is None:
        return None

    candidate = str(code).strip()
    if not candidate:
        return None

    if candidate.isdigit():
        padded = candidate.zfill(3)
        return padded if padded in _NUMERIC_TO_DISPLAY else None

    candidate = candidate.upper()
    if len(candidate) == 3:
        return _ALPHA3_TO_NUMERIC.get(candidate)
    if len(candidate) == 2:
        return _ALPHA2_TO_NUMERIC.get(candidate)
    return None


def display_for(numeric: str) -> Optional[str]:
    """ISO short name for a numeric country code, or ``None`` if unknown."""
    return _NUMERIC_TO_DISPLAY.get(numeric)
