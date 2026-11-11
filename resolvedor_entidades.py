"""
agente_futbol/resolvedor_entidades.py
───────────────────────────────────────────────────────────────────────────────
PUNTO 31: SISTEMA UNIVERSAL DE RESOLUCIÓN DE ENTIDADES
FLASHSCORE (ES) <-> FOTMOB (NATIVO/EN) PARA +110 PAÍSES Y +250 LIGAS.

Responsabilidades:
  1. Separar inteligentemente `/mercados [Liga Flashscore] [Equipo Local] vs [Equipo Visita]`
     ordenando el catálogo de ligas de mayor a menor longitud para que ligas compuestas
     (`2. Bundesliga`, `LaLiga EA Sports`, `Primera Federación - Grupo 1`, `Liga 1 - Clausura`)
     jamás contaminen el nombre del equipo local.
  2. Mapear las +250 ligas y +110 países de Flashscore a su código ISO-3 (`DEU`, `ESP`, `ARG`, etc.)
     y detectar automáticamente si la competición es Femenina (`es_femenino`) o Juvenil (`es_juvenil`).
  3. Traducir exónimos, hispanismos y alias de Flashscore al nombre oficial de FotMob
     con regla dura anti-colisión (`Bayer` != `Bayern`, `CA Platense [ARG]` != `Platense FC [HND]`,
     `1. FC Köln [DEU]` != `Colonia [URY]`).
  4. Puntuar y filtrar candidatos de FotMob (`validar_y_puntuar_candidato_fotmob`) aplicando
     descartes `-9999` a categorías no pedidas (Femenino/Juvenil/Filial), colisión Bayer/Bayern
     y homónimos de otros países, y bonos `+100` (país exacto) y `+50` (categoría exacta).
  5. Construir claves de caché en memoria indexadas por `(fotmob_team_id, pais_iso, es_femenino, es_juvenil)`.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple


def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""
    t = str(texto).strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9\s\.\-']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Mapeo de Ligas y Países Flashscore -> Código ISO-3 de FotMob
CATALOGO_LIGAS_FLASHSCORE: Dict[str, str] = {
    # EUROPA
    "alemania": "DEU", "bundesliga": "DEU", "1. bundesliga": "DEU", "2. bundesliga": "DEU", "2 bundesliga": "DEU", "3. liga": "DEU",
    "regionalliga nord": "DEU", "regionalliga nordost": "DEU", "regionalliga west": "DEU",
    "regionalliga bayern": "DEU", "regionalliga sudwest": "DEU", "oberliga": "DEU", "dfb youth league": "DEU",
    "dfb pokal": "DEU", "dfb-pokal": "DEU", "bundesliga femenina": "DEU", "2. bundesliga femenina": "DEU",
    "espana": "ESP", "laliga ea sports": "ESP", "la liga ea sports": "ESP", "laliga hypermotion": "ESP",
    "la liga hypermotion": "ESP", "laliga": "ESP", "la liga": "ESP", "laliga2": "ESP", "laliga 2": "ESP",
    "primera federacion": "ESP", "segunda federacion": "ESP", "tercera federacion": "ESP",
    "liga f": "ESP", "primera federacion femenina": "ESP", "copa del rey": "ESP",
    "inglaterra": "ENG", "premier league": "ENG", "english premier league": "ENG", "fa cup": "ENG", "efl cup": "ENG",
    "carabao cup": "ENG", "premier league 2": "ENG", "championship": "ENG", "efl championship": "ENG",
    "league one": "ENG", "league two": "ENG", "national league inglaterra": "ENG",
    "wsl": "ENG", "wsl 2": "ENG", "women's national league": "ENG", "fa cup femenina": "ENG",
    "italia": "ITA", "serie a": "ITA", "serie b": "ITA", "serie c": "ITA", "serie d": "ITA",
    "coppa italia": "ITA", "primavera 1": "ITA", "serie a femenina": "ITA", "serie b femenina": "ITA",
    "francia": "FRA", "ligue 1": "FRA", "ligue 2": "FRA", "national 1": "FRA", "coupe de france": "FRA",
    "premiere ligue femenina": "FRA", "seconde ligue femenina": "FRA",
    "portugal": "PRT", "liga portugal": "PRT", "primeira liga": "PRT", "liga portugal 2": "PRT", "liga 3": "PRT",
    "campeonato de portugal": "PRT", "liga next gen u23": "PRT", "campeonato nacional sub-19": "PRT", "taca de portugal": "PRT",
    "paises bajos": "NLD", "holanda": "NLD", "eredivisie": "NLD", "eerste divisie": "NLD",
    "keuken kampioen divisie": "NLD", "derde divisie": "NLD", "knvb beker": "NLD",
    "belgica": "BEL", "jupiler pro league": "BEL", "belgian pro league": "BEL", "challenger pro league": "BEL",
    "national division 1": "BEL", "second amateur division": "BEL",
    "austria": "AUT", "bundesliga austria": "AUT", "2. liga": "AUT", "regionalliga oeste": "AUT", "regionalliga north": "AUT",
    "suiza": "CHE", "super league": "CHE", "challenge league": "CHE", "promotion league": "CHE", "1. liga": "CHE",
    "rusia": "RUS", "premier league rusia": "RUS", "fnl": "RUS", "fnl 2": "RUS",
    "ucrania": "UKR", "premier league ucrania": "UKR", "persha liga": "UKR", "druha liga": "UKR",
    "polonia": "POL", "ekstraklasa": "POL", "i liga": "POL", "ii liga": "POL", "iii liga": "POL",
    "central youth league": "POL", "ekstraliga femenina": "POL",
    "republica checa": "CZE", "chance liga": "CZE", "chnl": "CZE", "3. cfl": "CZE", "3. msfl": "CZE", "4. liga": "CZE",
    "escocia": "SCO", "premiership": "SCO", "scottish premiership": "SCO", "swpl 1": "SCO",
    "irlanda": "IRL", "premier division irlanda": "IRL", "fai cup": "IRL",
    "grecia": "GRC", "super league grecia": "GRC", "super league 2": "GRC", "gamma ethniki": "GRC",
    "turquia": "TUR", "super lig": "TUR", "1. lig": "TUR", "2. lig": "TUR", "3. lig": "TUR",
    "rumania": "ROU", "liga 1 rumania": "ROU", "superliga rumania": "ROU",
    "serbia": "SRB", "mozzart bet super liga": "SRB", "mozzart bet prva liga": "SRB", "srpska liga": "SRB",
    "croacia": "HRV", "hnl": "HRV", "supersport hnl": "HRV", "prva nl": "HRV", "1. hnl women": "HRV",
    "dinamarca": "DNK", "superliga dinamarca": "DNK", "1st division": "DNK", "2a division": "DNK",
    "a-liga femenina": "DNK", "b-liga femenina": "DNK",
    "suecia": "SWE", "allsvenskan": "SWE", "superettan": "SWE", "division 1 - norra": "SWE",
    "damallsvenskan": "SWE", "elitettan femenina": "SWE",
    "noruega": "NOR", "eliteserien": "NOR", "obos-ligaen": "NOR", "toppserien femenina": "NOR",
    "finlandia": "FIN", "veikkausliiga": "FIN", "ykkosliiga": "FIN",
    "hungria": "HUN", "otp bank liga": "HUN", "nb i": "HUN", "nb ii": "HUN", "nb iii": "HUN",
    "bulgaria": "BGR", "efbet league": "BGR", "parva liga": "BGR", "vtora liga": "BGR", "third league": "BGR",
    "eslovaquia": "SVK", "nike liga": "SVK", "2. liga eslovaquia": "SVK",
    "eslovenia": "SVN", "prva liga": "SVN", "2. snl": "SVN", "3. snl": "SVN", "1. sznl": "SVN",
    "bosnia": "BIH", "wwin liga bih": "BIH", "prva liga - fbih": "BIH", "prva liga - rs": "BIH",
    "macedonia del norte": "MKD", "1. mfl": "MKD", "2. mfl": "MKD", "1. wfl": "MKD",
    "montenegro": "MNE", "prva crnogorska liga": "MNE", "druga liga": "MNE",
    "kosovo": "KOS", "superliga kosovo": "KOS", "liga e pare": "KOS",
    "albania": "ALB", "abissnet superiore": "ALB", "kategoria e pare": "ALB",
    "bielorrusia": "BLR", "vysshaya liga": "BLR", "pershaya liga": "BLR",
    "moldavia": "MDA", "super liga moldavia": "MDA",
    "letonia": "LVA", "virsliga": "LVA",
    "lituania": "LTU", "a lyga": "LTU", "toplyga": "LTU",
    "estonia": "EST", "meistriliiga": "EST", "esiliiga": "EST", "esiliiga b": "EST",
    "georgia": "GEO", "crystalbet erovnuli liga": "GEO", "erovnuli liga": "GEO",
    "armenia": "ARM", "fastex premier league": "ARM",
    "azerbaiyan": "AZE", "premyer liqa": "AZE",
    "chipre": "CYP", "cyprus league": "CYP", "first division chipre": "CYP",
    "malta": "MLT", "premier league malta": "MLT",
    "andorra": "AND", "primera divisio": "AND",
    "luxemburgo": "LUX", "bgl ligue": "LUX", "luxembourg cup": "LUX",
    "san marino": "SMR", "campionato sammarinese": "SMR",
    "gibraltar": "GIB", "national league": "GIB",
    # AMÉRICA
    "estados unidos": "USA", "usa": "USA", "mls": "USA", "major league soccer": "USA",
    "mls next pro": "USA", "usl championship": "USA", "usl league one": "USA",
    "nwsl": "USA", "usl super league": "USA", "us open cup": "USA",
    "mexico": "MEX", "liga mx": "MEX", "liga de expansion mx": "MEX", "liga premier": "MEX",
    "liga mx femenina": "MEX", "liga mx femenil": "MEX", "liga mx u21": "MEX",
    "argentina": "ARG", "liga profesional": "ARG", "liga profesional de argentina": "ARG",
    "copa de la liga profesional": "ARG", "primera nacional": "ARG", "torneo betano": "ARG",
    "torneo federal": "ARG", "copa argentina": "ARG",
    "brasil": "BRA", "brasileirao": "BRA", "brasileirao betano": "BRA",
    "brasileirao serie a": "BRA", "brasileirao serie b": "BRA", "brasileirao serie c": "BRA",
    "serie a de brasil": "BRA", "serie b de brasil": "BRA", "serie c de brasil": "BRA",
    "serie a brasil": "BRA", "serie b brasil": "BRA", "copa do brasil": "BRA",
    "carioca": "BRA", "catarinense": "BRA", "gaucho": "BRA", "goiano": "BRA", "mineiro": "BRA", "pernambucano": "BRA",
    "paulista": "BRA", "baiano": "BRA", "cearense": "BRA", "paranaense": "BRA",
    "sergipano": "BRA", "copa parana": "BRA", "copa paraiba": "BRA", "copa santa catarina": "BRA", "paulista sub-20": "BRA",
    "chile": "CHL", "liga de primera": "CHL", "primera division chile": "CHL", "campeonato chileno": "CHL",
    "primera b chile": "CHL", "segunda division": "CHL", "copa chile": "CHL",
    "colombia": "COL", "primera a": "COL", "liga betplay": "COL", "liga betplay dimayor": "COL",
    "torneo betplay": "COL", "primera b": "COL", "copa betplay": "COL",
    "copa colombia": "COL", "copa betplay dimayor": "COL", "superliga colombia": "COL",
    "ecuador": "ECU", "liga pro": "ECU", "ligapro": "ECU", "liga pro serie b": "ECU", "copa ecuador": "ECU",
    "peru": "PER", "liga 1": "PER", "liga 1 te apuesto": "PER", "liga 1 peru": "PER", "liga 1 de peru": "PER",
    "liga 2 peru": "PER", "liga 2 de peru": "PER", "copa peru": "PER", "liga femenina": "PER",
    "bolivia": "BOL", "division profesional": "BOL", "copa pacena": "BOL", "copa simon bolivar": "BOL",
    "panama": "PAN", "lpf": "PAN", "lpf apertura": "PAN", "lpf clausura": "PAN", "liga panamena": "PAN",
    "paraguay": "PRY", "copa de primera": "PRY", "primera division paraguay": "PRY", "division intermedia": "PRY", "copa paraguay": "PRY",
    "venezuela": "VEN", "liga futve": "VEN", "liga futve 2": "VEN",
    "uruguay": "URY", "primera division uruguay": "URY", "primera division": "URY", "liga auf uruguaya": "URY", "liga auf": "URY",
    "copa auf uruguay": "URY", "copa uruguay": "URY", "copa auf": "URY",
    "costa rica": "CRI", "primera division costa rica": "CRI", "liga promerica": "CRI", "liga de ascenso": "CRI",
    "guatemala": "GTM", "liga nacional guatemala": "GTM",
    "honduras": "HND", "liga nacional honduras": "HND",
    "el salvador": "SLV", "primera division el salvador": "SLV",
    "jamaica": "JAM", "premier league jamaica": "JAM",
    "canada": "CAN", "canadian premier league": "CAN", "nsl women": "CAN",
    "surinam": "SUR", "sml": "SUR",
    "aruba": "ABW", "division di honor": "ABW", "antigua y barbuda": "ATG", "abfa": "ATG",
    # ASIA, MEDIO ORIENTE Y OCEANÍA
    "japon": "JPN", "j1 league": "JPN", "j2 league": "JPN", "j3 league": "JPN", "we league femenina": "JPN",
    "corea del sur": "KOR", "k league 1": "KOR", "k league 2": "KOR", "k3 league": "KOR", "k4 league": "KOR",
    "china": "CHN", "super league china": "CHN", "chinese super league": "CHN", "china league one": "CHN",
    "australia": "AUS", "a-league": "AUS", "a-league women": "AUS",
    "arabia saudi": "SAU", "saudi pro league": "SAU", "first division arabia saudi": "SAU",
    "catar": "QAT", "qsl": "QAT", "stars league catar": "QAT",
    "emiratos arabes unidos": "ARE", "uae league": "ARE", "adnoc pro league": "ARE", "pro league u23": "ARE",
    "india": "IND", "indian super league": "IND", "isl": "IND", "i-league": "IND", "i-league 3": "IND",
    "indonesia": "IDN", "liga 1 indonesia": "IDN",
    "tailandia": "THA", "thai league 1": "THA", "thai league 2": "THA",
    "vietnam": "VNM", "v.league 1": "VNM", "v.league 2": "VNM",
    "malasia": "MYS", "super league malasia": "MYS",
    "filipinas": "PHL", "pfl": "PHL", "hong kong": "HKG", "premier league hong kong": "HKG",
    "irak": "IRQ", "stars league": "IRQ",
    "iran": "IRN", "persian gulf pro league": "IRN", "azadegan league": "IRN",
    "israel": "ISR", "ligat ha'al": "ISR", "leumit league": "ISR", "liga alef norte": "ISR",
    "jordania": "JOR", "kuwait": "KWT", "libano": "LBN",
    "uzbekistan": "UZB", "super league uzbekistan": "UZB", "pro liga": "UZB", "pro liga uzbekistan": "UZB",
    "kirguistan": "KGZ", "premier liga": "KGZ", "turkmenistan": "TKM", "yokary liga": "TKM",
    # ÁFRICA
    "egipto": "EGY", "premier league egipto": "EGY", "egypt cup": "EGY",
    "marruecos": "MAR", "botola pro": "MAR", "botola 2": "MAR",
    "argelia": "DZA", "ligue 1 argelia": "DZA",
    "tunez": "TUN", "ligue professionnelle 1": "TUN",
    "sudafrica": "ZAF", "betway premiership": "ZAF", "psl": "ZAF", "diski challenge": "ZAF", "carling knockout": "ZAF",
    "nigeria": "NGA", "npfl": "NGA", "nnl": "NGA",
    "ghana": "GHA", "premier league ghana": "GHA",
    "kenia": "KEN", "fkf premier league": "KEN",
    "uganda": "UGA", "premier league uganda": "UGA", "big league": "UGA",
    "ruanda": "RWA", "zambia": "ZMB",
    "zimbabue": "ZWE", "premier soccer league": "ZWE", "mozambique": "MOZ", "mocambola": "MOZ",
    "angola": "AGO", "girabola": "AGO", "camerun": "CMR", "elite one": "CMR", "elite two": "CMR",
    "costa de marfil": "CIV", "senegal": "SEN", "mali": "MLI", "premiere division": "MLI",
    "burkina faso": "BFA", "niger": "NER", "super ligue": "NER", "benin": "BEN",
    "mauritania": "MRT", "sierra leona": "SLE", "liberia": "LBR", "lfa first division": "LBR",
    "burundi": "BDI", "primus league": "BDI", "rd congo": "COD",
    # INTERNACIONALES (Permiten cualquier país del continente)
    "champions league": "INT", "uefa champions league": "INT", "ucl": "INT",
    "europa league": "INT", "uefa europa league": "INT", "uel": "INT",
    "conference league": "INT", "uefa conference league": "INT", "uecl": "INT",
    "copa libertadores": "INT", "conmebol libertadores": "INT", "libertadores": "INT",
    "copa sudamericana": "INT", "conmebol sudamericana": "INT", "sudamericana": "INT",
    "concacaf": "INT", "concacaf champions cup": "INT", "afc champions league": "INT", "caf champions league": "INT",
}


# Diccionario de Exónimos, Hispanismos y Alias de Flashscore -> (Nombre Oficial FotMob, Código ISO-3)
DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB: Dict[str, Tuple[str, str]] = {
    # ALEMANIA (Evita para siempre confundir Colonia o Bayer Leverkusen con Bayern München)
    "colonia": ("1. FC Köln", "DEU"),
    "fc koln": ("1. FC Köln", "DEU"),
    "1. fc koln": ("1. FC Köln", "DEU"),
    "koln": ("1. FC Köln", "DEU"),
    "bayer leverkusen": ("Bayer 04 Leverkusen", "DEU"),
    "leverkusen": ("Bayer 04 Leverkusen", "DEU"),
    "bayer 04 leverkusen": ("Bayer 04 Leverkusen", "DEU"),
    "bayer 04": ("Bayer 04 Leverkusen", "DEU"),
    "bayer": ("Bayer 04 Leverkusen", "DEU"),
    "bayern": ("FC Bayern München", "DEU"),
    "bayern munich": ("FC Bayern München", "DEU"),
    "bayern munchen": ("FC Bayern München", "DEU"),
    "fc bayern": ("FC Bayern München", "DEU"),
    "fc bayern munchen": ("FC Bayern München", "DEU"),
    "fc bayern munich": ("FC Bayern München", "DEU"),
    "borussia m'gladbach": ("Borussia Mönchengladbach", "DEU"),
    "m'gladbach": ("Borussia Mönchengladbach", "DEU"),
    "gladbach": ("Borussia Mönchengladbach", "DEU"),
    "monchengladbach": ("Borussia Mönchengladbach", "DEU"),
    "borussia monchengladbach": ("Borussia Mönchengladbach", "DEU"),
    "borussia dortmund": ("Borussia Dortmund", "DEU"),
    "dortmund": ("Borussia Dortmund", "DEU"),
    "maguncia": ("1. FSV Mainz 05", "DEU"),
    "mainz": ("1. FSV Mainz 05", "DEU"),
    "mainz 05": ("1. FSV Mainz 05", "DEU"),
    "1. fsv mainz 05": ("1. FSV Mainz 05", "DEU"),
    "friburgo": ("SC Freiburg", "DEU"),
    "freiburg": ("SC Freiburg", "DEU"),
    "sc freiburg": ("SC Freiburg", "DEU"),
    "eintracht francfort": ("Eintracht Frankfurt", "DEU"),
    "eintracht de francfort": ("Eintracht Frankfurt", "DEU"),
    "eintracht frankfurt": ("Eintracht Frankfurt", "DEU"),
    "francfort": ("Eintracht Frankfurt", "DEU"),
    "frankfurt": ("Eintracht Frankfurt", "DEU"),
    "stuttgart": ("VfB Stuttgart", "DEU"),
    "estuttgart": ("VfB Stuttgart", "DEU"),
    "vfb stuttgart": ("VfB Stuttgart", "DEU"),
    "augsburgo": ("FC Augsburg", "DEU"),
    "augsburg": ("FC Augsburg", "DEU"),
    "fc augsburg": ("FC Augsburg", "DEU"),
    "wolfsburgo": ("VfL Wolfsburg", "DEU"),
    "wolfsburg": ("VfL Wolfsburg", "DEU"),
    "vfl wolfsburg": ("VfL Wolfsburg", "DEU"),
    "hamburgo": ("Hamburger SV", "DEU"),
    "hamburger sv": ("Hamburger SV", "DEU"),
    "hsv": ("Hamburger SV", "DEU"),
    "nuremberg": ("1. FC Nürnberg", "DEU"),
    "nurnberg": ("1. FC Nürnberg", "DEU"),
    "1. fc nurnberg": ("1. FC Nürnberg", "DEU"),
    "schalke": ("FC Schalke 04", "DEU"),
    "schalke 04": ("FC Schalke 04", "DEU"),
    "fc schalke 04": ("FC Schalke 04", "DEU"),
    "hertha": ("Hertha BSC", "DEU"),
    "hertha berlin": ("Hertha BSC", "DEU"),
    "hertha bsc": ("Hertha BSC", "DEU"),
    "union berlin": ("1. FC Union Berlin", "DEU"),
    "1. fc union berlin": ("1. FC Union Berlin", "DEU"),
    "werder bremen": ("SV Werder Bremen", "DEU"),
    "sv werder bremen": ("SV Werder Bremen", "DEU"),
    "bremen": ("SV Werder Bremen", "DEU"),
    "rb leipzig": ("RB Leipzig", "DEU"),
    "leipzig": ("RB Leipzig", "DEU"),
    "st. pauli": ("FC St. Pauli", "DEU"),
    "st pauli": ("FC St. Pauli", "DEU"),
    "sankt pauli": ("FC St. Pauli", "DEU"),
    "fc st. pauli": ("FC St. Pauli", "DEU"),
    "bochum": ("VfL Bochum", "DEU"),
    "vfl bochum": ("VfL Bochum", "DEU"),
    "heidenheim": ("1. FC Heidenheim", "DEU"),
    "1. fc heidenheim": ("1. FC Heidenheim", "DEU"),
    "holstein kiel": ("Holstein Kiel", "DEU"),
    "kiel": ("Holstein Kiel", "DEU"),
    "hoffenheim": ("TSG Hoffenheim", "DEU"),
    "tsg hoffenheim": ("TSG Hoffenheim", "DEU"),
    "hannover": ("Hannover 96", "DEU"),
    "hannover 96": ("Hannover 96", "DEU"),
    "kaiserslautern": ("1. FC Kaiserslautern", "DEU"),
    "1. fc kaiserslautern": ("1. FC Kaiserslautern", "DEU"),
    "karlsruher": ("Karlsruher SC", "DEU"),
    "karlsruher sc": ("Karlsruher SC", "DEU"),
    "darmstadt": ("SV Darmstadt 98", "DEU"),
    "sv darmstadt 98": ("SV Darmstadt 98", "DEU"),
    "fortuna dusseldorf": ("Fortuna Düsseldorf", "DEU"),
    "dusseldorf": ("Fortuna Düsseldorf", "DEU"),
    "paderborn": ("SC Paderborn 07", "DEU"),
    "sc paderborn 07": ("SC Paderborn 07", "DEU"),
    "greuther furth": ("SpVgg Greuther Fürth", "DEU"),
    "magdeburgo": ("1. FC Magdeburg", "DEU"),
    "magdeburg": ("1. FC Magdeburg", "DEU"),
    "1. fc magdeburg": ("1. FC Magdeburg", "DEU"),
    "braunschweig": ("Eintracht Braunschweig", "DEU"),
    "eintracht braunschweig": ("Eintracht Braunschweig", "DEU"),
    # ESPAÑA
    "atletico madrid": ("Atlético Madrid", "ESP"),
    "atletico de madrid": ("Atlético Madrid", "ESP"),
    "atleti": ("Atlético Madrid", "ESP"),
    "athletic": ("Athletic Club", "ESP"),
    "athletic club": ("Athletic Club", "ESP"),
    "athletic bilbao": ("Athletic Club", "ESP"),
    "athletic de bilbao": ("Athletic Club", "ESP"),
    "alaves": ("Deportivo Alavés", "ESP"),
    "deportivo alaves": ("Deportivo Alavés", "ESP"),
    "celta": ("Celta Vigo", "ESP"),
    "celta vigo": ("Celta Vigo", "ESP"),
    "celta de vigo": ("Celta Vigo", "ESP"),
    "rc celta": ("Celta Vigo", "ESP"),
    "betis": ("Real Betis", "ESP"),
    "real betis": ("Real Betis", "ESP"),
    "real sociedad": ("Real Sociedad", "ESP"),
    "rayo": ("Rayo Vallecano", "ESP"),
    "rayo vallecano": ("Rayo Vallecano", "ESP"),
    "espanyol": ("RCD Espanyol", "ESP"),
    "espanol": ("RCD Espanyol", "ESP"),
    "rcd espanyol": ("RCD Espanyol", "ESP"),
    "mallorca": ("RCD Mallorca", "ESP"),
    "rcd mallorca": ("RCD Mallorca", "ESP"),
    "girona": ("Girona FC", "ESP"),
    "girona fc": ("Girona FC", "ESP"),
    "leganes": ("CD Leganés", "ESP"),
    "cd leganes": ("CD Leganés", "ESP"),
    "valladolid": ("Real Valladolid", "ESP"),
    "real valladolid": ("Real Valladolid", "ESP"),
    "las palmas": ("UD Las Palmas", "ESP"),
    "ud las palmas": ("UD Las Palmas", "ESP"),
    "sporting gijon": ("Sporting Gijón", "ESP"),
    "real zaragoza": ("Real Zaragoza", "ESP"),
    "zaragoza": ("Real Zaragoza", "ESP"),
    "racing santander": ("Racing Santander", "ESP"),
    "deportivo la coruna": ("Deportivo de La Coruña", "ESP"),
    "deportivo a coruna": ("Deportivo de La Coruña", "ESP"),
    "deportivo de la coruna": ("Deportivo de La Coruña", "ESP"),
    "deportivo": ("Deportivo La Coruña", "ESP"),
    "lugo": ("CD Lugo", "ESP"),
    "cd lugo": ("CD Lugo", "ESP"),
    "levante": ("Levante", "ESP"),
    "levante ud": ("Levante", "ESP"),
    "elche": ("Elche", "ESP"),
    "elche cf": ("Elche", "ESP"),
    "osasuna": ("Osasuna", "ESP"),
    "ca osasuna": ("Osasuna", "ESP"),
    "getafe": ("Getafe", "ESP"),
    "getafe cf": ("Getafe", "ESP"),
    "sevilla": ("Sevilla", "ESP"),
    "sevilla fc": ("Sevilla", "ESP"),
    "valencia": ("Valencia", "ESP"),
    "valencia cf": ("Valencia", "ESP"),
    "ud tavernes": ("UD Tavernes", "ESP"),
    "tavernes": ("UD Tavernes", "ESP"),
    "villarreal": ("Villarreal", "ESP"),
    "villarreal cf": ("Villarreal", "ESP"),
    "barcelona": ("Barcelona", "ESP"),
    "fc barcelona": ("Barcelona", "ESP"),
    "barca": ("Barcelona", "ESP"),
    "real madrid": ("Real Madrid", "ESP"),
    "real oviedo": ("Real Oviedo", "ESP"),
    "oviedo": ("Real Oviedo", "ESP"),
    # ITALIA
    "napoles": ("SSC Napoli", "ITA"),
    "napoli": ("SSC Napoli", "ITA"),
    "ssc napoli": ("SSC Napoli", "ITA"),
    "bolonia": ("Bologna", "ITA"),
    "bologna": ("Bologna", "ITA"),
    "genova": ("Genoa", "ITA"),
    "genoa": ("Genoa", "ITA"),
    "milan": ("AC Milan", "ITA"),
    "ac milan": ("AC Milan", "ITA"),
    "inter": ("Internazionale", "ITA"),
    "inter de milan": ("Internazionale", "ITA"),
    "internazionale": ("Internazionale", "ITA"),
    "roma": ("AS Roma", "ITA"),
    "as roma": ("AS Roma", "ITA"),
    "lazio": ("Lazio", "ITA"),
    "ss lazio": ("Lazio", "ITA"),
    "juventus": ("Juventus", "ITA"),
    "fiorentina": ("Fiorentina", "ITA"),
    "florentina": ("Fiorentina", "ITA"),
    "atalanta": ("Atalanta", "ITA"),
    "torino": ("Torino", "ITA"),
    "veneza": ("Venezia", "ITA"),
    "venecia": ("Venezia", "ITA"),
    "venezia": ("Venezia", "ITA"),
    # FRANCIA
    "marsella": ("Olympique de Marseille", "FRA"),
    "olympique marsella": ("Olympique de Marseille", "FRA"),
    "olympique de marseille": ("Olympique de Marseille", "FRA"),
    "niza": ("OGC Nice", "FRA"),
    "nice": ("OGC Nice", "FRA"),
    "ogc nice": ("OGC Nice", "FRA"),
    "estrasburgo": ("RC Strasbourg", "FRA"),
    "strasbourg": ("RC Strasbourg", "FRA"),
    "rc strasbourg": ("RC Strasbourg", "FRA"),
    "psg": ("Paris Saint-Germain", "FRA"),
    "paris sg": ("Paris Saint-Germain", "FRA"),
    "paris saint-germain": ("Paris Saint-Germain", "FRA"),
    "paris saint germain": ("Paris Saint-Germain", "FRA"),
    "lyon": ("Olympique Lyonnais", "FRA"),
    "olympique lyonnais": ("Olympique Lyonnais", "FRA"),
    "monaco": ("AS Monaco", "FRA"),
    "as monaco": ("AS Monaco", "FRA"),
    "lille": ("Lille OSC", "FRA"),
    "lille osc": ("Lille OSC", "FRA"),
    "burdeos": ("Bordeaux", "FRA"),
    "bordeaux": ("Bordeaux", "FRA"),
    # INGLATERRA
    "manchester united": ("Manchester United", "ENG"),
    "man utd": ("Manchester United", "ENG"),
    "manchester city": ("Manchester City", "ENG"),
    "man city": ("Manchester City", "ENG"),
    "liverpool": ("Liverpool", "ENG"),
    "arsenal": ("Arsenal", "ENG"),
    "chelsea": ("Chelsea", "ENG"),
    "tottenham": ("Tottenham Hotspur", "ENG"),
    "tottenham hotspur": ("Tottenham Hotspur", "ENG"),
    "newcastle": ("Newcastle United", "ENG"),
    "newcastle united": ("Newcastle United", "ENG"),
    "aston villa": ("Aston Villa", "ENG"),
    "west ham": ("West Ham United", "ENG"),
    "west ham united": ("West Ham United", "ENG"),
    "brighton": ("Brighton & Hove Albion", "ENG"),
    "wolves": ("Wolverhampton Wanderers", "ENG"),
    "wolverhampton": ("Wolverhampton Wanderers", "ENG"),
    "fulham": ("Fulham", "ENG"),
    "fc fulham": ("Fulham", "ENG"),
    "hull": ("Hull City", "ENG"),
    "hull city": ("Hull City", "ENG"),
    "nottingham": ("Nottingham Forest", "ENG"),
    "nottingham forest": ("Nottingham Forest", "ENG"),
    # PORTUGAL, PAÍSES BAJOS, BÉLGICA Y RESTO DE EUROPA
    "oporto": ("FC Porto", "PRT"),
    "porto": ("FC Porto", "PRT"),
    "fc porto": ("FC Porto", "PRT"),
    "sporting lisboa": ("Sporting CP", "PRT"),
    "sporting cp": ("Sporting CP", "PRT"),
    "sporting de lisboa": ("Sporting CP", "PRT"),
    "sporting de portugal": ("Sporting CP", "PRT"),
    "benfica": ("SL Benfica", "PRT"),
    "sl benfica": ("SL Benfica", "PRT"),
    "braga": ("SC Braga", "PRT"),
    "sc braga": ("SC Braga", "PRT"),
    "vitoria guimaraes": ("Vitória SC", "PRT"),
    "brujas": ("Club Brugge", "BEL"),
    "club brujas": ("Club Brugge", "BEL"),
    "club brugge": ("Club Brugge", "BEL"),
    "gante": ("KAA Gent", "BEL"),
    "kaa gent": ("KAA Gent", "BEL"),
    "amberes": ("Royal Antwerp", "BEL"),
    "royal antwerp": ("Royal Antwerp", "BEL"),
    "malinas": ("KV Mechelen", "BEL"),
    "kv mechelen": ("KV Mechelen", "BEL"),
    "copenhague": ("FC København", "DNK"),
    "fc copenhague": ("FC København", "DNK"),
    "fc kobenhavn": ("FC København", "DNK"),
    "estrella roja": ("Crvena Zvezda", "SRB"),
    "crvena zvezda": ("Crvena Zvezda", "SRB"),
    "sparta praga": ("AC Sparta Praha", "CZE"),
    "ac sparta praha": ("AC Sparta Praha", "CZE"),
    "slavia praga": ("SK Slavia Praha", "CZE"),
    "sk slavia praha": ("SK Slavia Praha", "CZE"),
    "viktoria plzen": ("FC Viktoria Plzeň", "CZE"),
    "fc viktoria plzen": ("FC Viktoria Plzeň", "CZE"),
    "dinamo zagreb": ("GNK Dinamo Zagreb", "HRV"),
    "gnk dinamo zagreb": ("GNK Dinamo Zagreb", "HRV"),
    "legia varsovia": ("Legia Warszawa", "POL"),
    "legia warszawa": ("Legia Warszawa", "POL"),
    "basilea": ("FC Basel", "CHE"),
    "fc basel": ("FC Basel", "CHE"),
    "zurich": ("FC Zürich", "CHE"),
    "fc zurich": ("FC Zürich", "CHE"),
    "fenerbahce": ("Fenerbahçe", "TUR"),
    "galatasaray": ("Galatasaray", "TUR"),
    "besiktas": ("Beşiktaş", "TUR"),
    "olympiacos": ("Olympiacos", "GRC"),
    "panathinaikos": ("Panathinaikos", "GRC"),
    "paok": ("PAOK", "GRC"),
    # SUDAMÉRICA Y MÉXICO
    "platense": ("CA Platense", "ARG"),
    "ca platense": ("CA Platense", "ARG"),
    "club atletico platense": ("CA Platense", "ARG"),
    "san lorenzo": ("San Lorenzo", "ARG"),
    "ca san lorenzo": ("San Lorenzo", "ARG"),
    "river plate": ("River Plate", "ARG"),
    "boca juniors": ("Boca Juniors", "ARG"),
    "independiente": ("CA Independiente", "ARG"),
    "ca independiente": ("CA Independiente", "ARG"),
    "racing club": ("Racing Club", "ARG"),
    "deportivo riestra": ("Deportivo Riestra", "ARG"),
    "riestra": ("Deportivo Riestra", "ARG"),
    "alianza lima": ("Alianza Lima", "PER"),
    "universitario": ("Universitario de Deportes", "PER"),
    "universitario de deportes": ("Universitario de Deportes", "PER"),
    "sporting cristal": ("Sporting Cristal", "PER"),
    "melgar": ("FBC Melgar", "PER"),
    "fbc melgar": ("FBC Melgar", "PER"),
    "colo colo": ("Colo-Colo", "CHL"),
    "colo-colo": ("Colo-Colo", "CHL"),
    "universidad de chile": ("Universidad de Chile", "CHL"),
    "universidad catolica": ("Universidad Católica", "CHL"),
    # COLOMBIA (Primera A y Torneo BetPlay)
    "atletico nacional": ("Atlético Nacional", "COL"),
    "millonarios": ("Millonarios", "COL"),
    "america de cali": ("América de Cali", "COL"),
    "junior": ("Junior FC", "COL"),
    "junior fc": ("Junior FC", "COL"),
    "junior barranquilla": ("Junior FC", "COL"),
    "junior de barranquilla": ("Junior FC", "COL"),
    "atletico junior": ("Junior FC", "COL"),
    "cucuta": ("Cúcuta Deportivo", "COL"),
    "cucuta deportivo": ("Cúcuta Deportivo", "COL"),
    "once caldas": ("Once Caldas", "COL"),
    "deportivo cali": ("Deportivo Cali", "COL"),
    "cali": ("Deportivo Cali", "COL"),
    "independiente medellin": ("Independiente Medellín", "COL"),
    "medellin": ("Independiente Medellín", "COL"),
    "dim": ("Independiente Medellín", "COL"),
    "santa fe": ("Independiente Santa Fe", "COL"),
    "independiente santa fe": ("Independiente Santa Fe", "COL"),
    "tolima": ("Deportes Tolima", "COL"),
    "deportes tolima": ("Deportes Tolima", "COL"),
    "bucaramanga": ("Atlético Bucaramanga", "COL"),
    "atletico bucaramanga": ("Atlético Bucaramanga", "COL"),
    "pereira": ("Deportivo Pereira", "COL"),
    "deportivo pereira": ("Deportivo Pereira", "COL"),
    "la equidad": ("La Equidad", "COL"),
    "equidad": ("La Equidad", "COL"),
    "aguilas doradas": ("Águilas Doradas", "COL"),
    "envigado": ("Envigado FC", "COL"),
    "deportivo pasto": ("Deportivo Pasto", "COL"),
    "pasto": ("Deportivo Pasto", "COL"),
    "patriotas": ("Patriotas Boyacá", "COL"),
    "boyaca chico": ("Boyacá Chicó", "COL"),
    "fortaleza ceif": ("Fortaleza CEIF", "COL"),
    "alianza fc": ("Alianza FC", "COL"),
    "llaneros": ("Llaneros FC", "COL"),
    "real cartagena": ("Real Cartagena", "COL"),
    "quindio": ("Deportes Quindío", "COL"),
    "huila": ("Atlético Huila", "COL"),
    # BOLIVIA (División Profesional) — Prohibido borrar el apellido/ciudad ("de Vinto", "Potosí", "Oruro", "Bulo Bulo")
    "aurora": ("Aurora", "BOL"),
    "club aurora": ("Aurora", "BOL"),
    "universitario de vinto": ("Universitario de Vinto", "BOL"),
    "universitario vinto": ("Universitario de Vinto", "BOL"),
    "fc universitario": ("Universitario de Vinto", "BOL"),
    "bolivar": ("Bolívar", "BOL"),
    "the strongest": ("The Strongest", "BOL"),
    "always ready": ("Always Ready", "BOL"),
    "wilstermann": ("Jorge Wilstermann", "BOL"),
    "jorge wilstermann": ("Jorge Wilstermann", "BOL"),
    "blooming": ("Blooming", "BOL"),
    "oriente petrolero": ("Oriente Petrolero", "BOL"),
    "nacional potosi": ("Nacional Potosí", "BOL"),
    "real potosi": ("Real Potosí", "BOL"),
    "real oruro": ("Real Oruro", "BOL"),
    "independiente petrolero": ("Independiente Petrolero", "BOL"),
    "real tomayapo": ("Real Tomayapo", "BOL"),
    "tomayapo": ("Real Tomayapo", "BOL"),
    "guabira": ("Guabirá", "BOL"),
    "royal pari": ("Royal Pari", "BOL"),
    "real santa cruz": ("Real Santa Cruz", "BOL"),
    "gv san jose": ("GV San José", "BOL"),
    "gualberto villarroel": ("GV San José", "BOL"),
    "san jose": ("GV San José", "BOL"),
    "san antonio bulo bulo": ("San Antonio Bulo Bulo", "BOL"),
    "bulo bulo": ("San Antonio Bulo Bulo", "BOL"),
    # URUGUAY (Primera División / Liga AUF Uruguaya)
    "deportivo maldonado": ("Deportivo Maldonado", "URY"),
    "maldonado": ("Deportivo Maldonado", "URY"),
    "nacional": ("Nacional", "URY"),
    "club nacional": ("Nacional", "URY"),
    "nacional de montevideo": ("Nacional", "URY"),
    "penarol": ("Peñarol", "URY"),
    "ca penarol": ("Peñarol", "URY"),
    "defensor sporting": ("Defensor Sporting", "URY"),
    "danubio": ("Danubio", "URY"),
    "boston river": ("Boston River", "URY"),
    "cerro largo": ("Cerro Largo", "URY"),
    "liverpool montevideo": ("Liverpool FC", "URY"),
    "liverpool m.": ("Liverpool FC", "URY"),
    "liverpool m": ("Liverpool FC", "URY"),
    "liverpool de montevideo": ("Liverpool FC", "URY"),
    "juventud": ("Juventud de Las Piedras", "URY"),
    "juventud de las piedras": ("Juventud de Las Piedras", "URY"),
    "ca juventud": ("Juventud de Las Piedras", "URY"),
    "juventud las piedras": ("Juventud de Las Piedras", "URY"),
    "montevideo wanderers": ("Montevideo Wanderers", "URY"),
    "miramar misiones": ("Miramar Misiones", "URY"),
    "rampla juniors": ("Rampla Juniors", "URY"),
    "progreso": ("Progreso", "URY"),
    # UZBEKISTÁN (Super League y Pro Liga)
    "gazalkent": ("FK Gazalkent", "UZB"),
    "fk gazalkent": ("FK Gazalkent", "UZB"),
    "respublika fa": ("Respublika FA", "UZB"),
    "paxtakor": ("Pakhtakor Tashkent", "UZB"),
    "pakhtakor": ("Pakhtakor Tashkent", "UZB"),
    "nasaf": ("FC Nasaf", "UZB"),
    "navbahor": ("Navbahor Namangan", "UZB"),
    "neftchi": ("Neftchi Fergana", "UZB"),
    "agmk": ("FC AGMK", "UZB"),
    "sogdiana": ("Sogdiana Jizzakh", "UZB"),
    "bunyodkor": ("FC Bunyodkor", "UZB"),
}

# IDs oficiales conocidos en FotMob para indexar caché de forma determinista incluso antes de llamada HTTP
FOTMOB_IDS_CONOCIDOS: Dict[Tuple[str, str], str] = {
    ("1. FC Köln", "DEU"): "8722",
    ("Bayer 04 Leverkusen", "DEU"): "8178",
    ("FC Bayern München", "DEU"): "9823",
    ("Borussia Dortmund", "DEU"): "9789",
    ("Borussia Mönchengladbach", "DEU"): "9788",
    ("1. FSV Mainz 05", "DEU"): "9905",
    ("SC Freiburg", "DEU"): "8358",
    ("Eintracht Frankfurt", "DEU"): "9810",
    ("VfB Stuttgart", "DEU"): "10269",
    ("VfL Wolfsburg", "DEU"): "8721",
    ("Hamburger SV", "DEU"): "9790",
    ("1. FC Magdeburg", "DEU"): "8188",
    ("RB Leipzig", "DEU"): "178475",
    ("Celta Vigo", "ESP"): "9910",
    ("Levante", "ESP"): "8581",
    ("Atlético Madrid", "ESP"): "9906",
    ("Real Madrid", "ESP"): "8633",
    ("Barcelona", "ESP"): "8634",
    ("Real Sociedad", "ESP"): "8560",
    ("Rayo Vallecano", "ESP"): "8370",
    ("Deportivo Alavés", "ESP"): "9866",
    ("Sevilla", "ESP"): "8302",
    ("Elche", "ESP"): "10268",
    ("Deportivo La Coruña", "ESP"): "9783",
    ("CD Lugo", "ESP"): "9862",
    ("San Lorenzo", "ARG"): "10080",
    ("CA Platense", "ARG"): "108854",
    ("River Plate", "ARG"): "10076",
    ("Boca Juniors", "ARG"): "10077",
    ("Alianza Lima", "PER"): "5806",
    ("Universitario de Deportes", "PER"): "5805",
    ("AS Roma", "ITA"): "8686",
    ("Juventus", "ITA"): "9885",
    ("AC Milan", "ITA"): "8564",
    ("Internazionale", "ITA"): "8636",
    ("Liverpool FC", "URY"): "7942",
    ("Liverpool", "ENG"): "8650",
    ("Juventud de Las Piedras", "URY"): "8390",
    ("Arsenal", "ENG"): "9825",
    ("Arsenal de Sarandí", "ARG"): "10084",
    ("Once Caldas", "COL"): "10091",
    ("Atlético Nacional", "COL"): "10093",
    ("Valencia", "ESP"): "10205",
    ("UD Tavernes", "ESP"): "1543888",
}

# Tabla de Desambiguación para Clubes con Nombres Homónimos o Multi-País
CLUBES_HOMONIMOS: Dict[str, List[Tuple[str, str]]] = {
    "liverpool": [("Liverpool FC", "URY"), ("Liverpool", "ENG")],
    "arsenal": [("Arsenal", "ENG"), ("Arsenal de Sarandí", "ARG")],
    "barcelona": [("Barcelona", "ESP"), ("Barcelona SC", "ECU")],
    "everton": [("Everton", "ENG"), ("Everton de Viña del Mar", "CHL")],
    "nacional": [("Nacional", "URY"), ("Atlético Nacional", "COL"), ("Club Nacional", "PRY")],
    "platense": [("CA Platense", "ARG"), ("Platense FC", "HND")],
    "santa fe": [("Independiente Santa Fe", "COL"), ("Unión de Santa Fe", "ARG")],
    "san lorenzo": [("San Lorenzo", "ARG"), ("San Lorenzo", "PRY")],
    "juventud": [("Juventud de Las Piedras", "URY")],
    "valencia": [("Valencia", "ESP"), ("Valencia FC", "VEN")],
    "universitario": [
        ("Universitario de Deportes", "PER"),
        ("Universitario de Vinto", "BOL"),
        ("Técnico Universitario", "ECU"),
        ("CD Universitario", "PAN"),
    ],
}

# Equivalencias entre códigos FIFA/FotMob/nombres en español/inglés -> Código ISO-3 oficial
MAPA_PAIS_A_ISO3: Dict[str, str] = {
    "deu": "DEU", "ger": "DEU", "alemania": "DEU", "germany": "DEU",
    "esp": "ESP", "spa": "ESP", "espana": "ESP", "spain": "ESP",
    "eng": "ENG", "inglaterra": "ENG", "england": "ENG",
    "ita": "ITA", "italia": "ITA", "italy": "ITA",
    "fra": "FRA", "francia": "FRA", "france": "FRA",
    "prt": "PRT", "por": "PRT", "portugal": "PRT",
    "nld": "NLD", "ned": "NLD", "holanda": "NLD", "paises bajos": "NLD", "netherlands": "NLD",
    "bel": "BEL", "belgica": "BEL", "belgium": "BEL",
    "aut": "AUT", "austria": "AUT",
    "che": "CHE", "sui": "CHE", "suiza": "CHE", "switzerland": "CHE",
    "rus": "RUS", "rusia": "RUS", "russia": "RUS",
    "ukr": "UKR", "ucrania": "UKR", "ukraine": "UKR",
    "pol": "POL", "polonia": "POL", "poland": "POL",
    "cze": "CZE", "republica checa": "CZE", "czech republic": "CZE", "czechia": "CZE",
    "sco": "SCO", "escocia": "SCO", "scotland": "SCO",
    "irl": "IRL", "irlanda": "IRL", "ireland": "IRL", "republic of ireland": "IRL",
    "grc": "GRC", "gre": "GRC", "grecia": "GRC", "greece": "GRC",
    "tur": "TUR", "turquia": "TUR", "turkey": "TUR", "turkiye": "TUR",
    "rou": "ROU", "rom": "ROU", "rumania": "ROU", "romania": "ROU",
    "srb": "SRB", "serbia": "SRB",
    "hrv": "HRV", "cro": "HRV", "croacia": "HRV", "croatia": "HRV",
    "dnk": "DNK", "den": "DNK", "dinamarca": "DNK", "denmark": "DNK",
    "swe": "SWE", "suecia": "SWE", "sweden": "SWE",
    "nor": "NOR", "noruega": "NOR", "norway": "NOR",
    "fin": "FIN", "finlandia": "FIN", "finland": "FIN",
    "hun": "HUN", "hungria": "HUN", "hungary": "HUN",
    "bgr": "BGR", "bul": "BGR", "bulgaria": "BGR",
    "svk": "SVK", "eslovaquia": "SVK", "slovakia": "SVK",
    "svn": "SVN", "slo": "SVN", "eslovenia": "SVN", "slovenia": "SVN",
    "bih": "BIH", "bos": "BIH", "bosnia": "BIH", "bosnia and herzegovina": "BIH",
    "mkd": "MKD", "macedonia del norte": "MKD", "north macedonia": "MKD",
    "mne": "MNE", "montenegro": "MNE",
    "kos": "KOS", "kosovo": "KOS",
    "alb": "ALB", "albania": "ALB",
    "blr": "BLR", "bielorrusia": "BLR", "belarus": "BLR",
    "mda": "MDA", "moldavia": "MDA", "moldova": "MDA",
    "lva": "LVA", "lat": "LVA", "letonia": "LVA", "latvia": "LVA",
    "ltu": "LTU", "lituania": "LTU", "lithuania": "LTU",
    "est": "EST", "estonia": "EST",
    "geo": "GEO", "georgia": "GEO",
    "arm": "ARM", "armenia": "ARM",
    "aze": "AZE", "azerbaiyan": "AZE", "azerbaijan": "AZE",
    "cyp": "CYP", "chipre": "CYP", "cyprus": "CYP",
    "mlt": "MLT", "malta": "MLT",
    "and": "AND", "andorra": "AND",
    "lux": "LUX", "luxemburgo": "LUX", "luxembourg": "LUX",
    "smr": "SMR", "san marino": "SMR",
    "gib": "GIB", "gibraltar": "GIB",
    "usa": "USA", "estados unidos": "USA", "united states": "USA", "eeuu": "USA",
    "mex": "MEX", "mexico": "MEX",
    "arg": "ARG", "argentina": "ARG",
    "bra": "BRA", "brasil": "BRA", "brazil": "BRA",
    "chl": "CHL", "chi": "CHL", "chile": "CHL",
    "col": "COL", "colombia": "COL",
    "ecu": "ECU", "ecuador": "ECU",
    "per": "PER", "peru": "PER",
    "bol": "BOL", "bolivia": "BOL",
    "pry": "PRY", "par": "PRY", "paraguay": "PRY",
    "ven": "VEN", "venezuela": "VEN",
    "ury": "URY", "uru": "URY", "uruguay": "URY",
    "cri": "CRI", "crc": "CRI", "costa rica": "CRI",
    "gtm": "GTM", "gua": "GTM", "guatemala": "GTM",
    "hnd": "HND", "hon": "HND", "honduras": "HND",
    "slv": "SLV", "esa": "SLV", "el salvador": "SLV",
    "pan": "PAN", "panama": "PAN",
    "jam": "JAM", "jamaica": "JAM",
    "can": "CAN", "canada": "CAN",
    "sur": "SUR", "surinam": "SUR", "suriname": "SUR",
    "abw": "ABW", "aruba": "ABW",
    "atg": "ATG", "antigua y barbuda": "ATG",
    "jpn": "JPN", "japon": "JPN", "japan": "JPN",
    "kor": "KOR", "corea del sur": "KOR", "south korea": "KOR",
    "chn": "CHN", "china": "CHN",
    "aus": "AUS", "australia": "AUS",
    "sau": "SAU", "ksa": "SAU", "arabia saudi": "SAU", "saudi arabia": "SAU",
    "qat": "QAT", "catar": "QAT", "qatar": "QAT",
    "are": "ARE", "uae": "ARE", "emiratos arabes unidos": "ARE",
    "ind": "IND", "india": "IND",
    "idn": "IDN", "indonesia": "IDN",
    "tha": "THA", "tailandia": "THA", "thailand": "THA",
    "vnm": "VNM", "vie": "VNM", "vietnam": "VNM",
    "mys": "MYS", "mas": "MYS", "malasia": "MYS", "malaysia": "MYS",
    "phl": "PHL", "phi": "PHL", "filipinas": "PHL", "philippines": "PHL",
    "hkg": "HKG", "hong kong": "HKG",
    "irq": "IRQ", "irak": "IRQ", "iraq": "IRQ",
    "irn": "IRN", "iri": "IRN", "iran": "IRN",
    "isr": "ISR", "israel": "ISR",
    "jor": "JOR", "jordania": "JOR", "jordan": "JOR",
    "kwt": "KWT", "kuw": "KWT", "kuwait": "KWT",
    "lbn": "LBN", "lib": "LBN", "libano": "LBN", "lebanon": "LBN",
    "uzb": "UZB", "uzbekistan": "UZB",
    "kgz": "KGZ", "kirguistan": "KGZ",
    "tkm": "TKM", "turkmenistan": "TKM",
    "egy": "EGY", "egipto": "EGY", "egypt": "EGY",
    "mar": "MAR", "marruecos": "MAR", "morocco": "MAR",
    "dza": "DZA", "alg": "DZA", "argelia": "DZA", "algeria": "DZA",
    "tun": "TUN", "tunez": "TUN", "tunisia": "TUN",
    "zaf": "ZAF", "rsa": "ZAF", "sudafrica": "ZAF", "south africa": "ZAF",
    "nga": "NGA", "nigeria": "NGA",
    "gha": "GHA", "ghana": "GHA",
    "ken": "KEN", "kenia": "KEN", "kenya": "KEN",
    "uga": "UGA", "uganda": "UGA",
    "rwa": "RWA", "ruanda": "RWA", "rwanda": "RWA",
    "zmb": "ZMB", "zam": "ZMB", "zambia": "ZMB",
    "zwe": "ZWE", "zim": "ZWE", "zimbabue": "ZWE", "zimbabwe": "ZWE",
    "moz": "MOZ", "mozambique": "MOZ",
    "ago": "AGO", "ang": "AGO", "angola": "AGO",
    "cmr": "CMR", "camerun": "CMR", "cameroon": "CMR",
    "civ": "CIV", "costa de marfil": "CIV", "ivory coast": "CIV",
    "sen": "SEN", "senegal": "SEN",
    "mli": "MLI", "mali": "MLI",
    "bfa": "BFA", "burkina faso": "BFA",
    "ner": "NER", "niger": "NER",
    "ben": "BEN", "benin": "BEN",
    "mrt": "MRT", "mauritania": "MRT",
    "sle": "SLE", "sierra leona": "SLE",
    "lbr": "LBR", "liberia": "LBR",
    "bdi": "BDI", "burundi": "BDI",
    "cod": "COD", "rd congo": "COD",
    "int": "INT", "europa": "INT", "sudamerica": "INT", "internacional": "INT",
}

MAPA_ISO3_A_PAIS_INTERNO: Dict[str, str] = {
    "DEU": "alemania",
    "ESP": "espana",
    "ENG": "inglaterra",
    "ITA": "italia",
    "FRA": "francia",
    "PRT": "portugal",
    "NLD": "holanda",
    "BEL": "belgica",
    "AUT": "austria",
    "CHE": "suiza",
    "RUS": "rusia",
    "UKR": "ucrania",
    "POL": "polonia",
    "CZE": "republica checa",
    "SCO": "escocia",
    "IRL": "irlanda",
    "GRC": "grecia",
    "TUR": "turquia",
    "ROU": "rumania",
    "SRB": "serbia",
    "HRV": "croacia",
    "DNK": "dinamarca",
    "SWE": "suecia",
    "NOR": "noruega",
    "FIN": "finlandia",
    "HUN": "hungria",
    "BGR": "bulgaria",
    "SVK": "eslovaquia",
    "SVN": "eslovenia",
    "BIH": "bosnia",
    "MKD": "macedonia del norte",
    "MNE": "montenegro",
    "KOS": "kosovo",
    "ALB": "albania",
    "BLR": "bielorrusia",
    "MDA": "moldavia",
    "LVA": "letonia",
    "LTU": "lituania",
    "EST": "estonia",
    "GEO": "georgia",
    "ARM": "armenia",
    "AZE": "azerbaiyan",
    "CYP": "chipre",
    "MLT": "malta",
    "AND": "andorra",
    "LUX": "luxemburgo",
    "SMR": "san marino",
    "GIB": "gibraltar",
    "USA": "usa",
    "MEX": "mexico",
    "ARG": "argentina",
    "BRA": "brasil",
    "CHL": "chile",
    "COL": "colombia",
    "ECU": "ecuador",
    "PER": "peru",
    "BOL": "bolivia",
    "PRY": "paraguay",
    "VEN": "venezuela",
    "URY": "uruguay",
    "CRI": "costa rica",
    "GTM": "guatemala",
    "HND": "honduras",
    "SLV": "el salvador",
    "PAN": "panama",
    "JAM": "jamaica",
    "CAN": "canada",
    "SUR": "surinam",
    "ABW": "aruba",
    "ATG": "antigua y barbuda",
    "JPN": "japon",
    "KOR": "corea del sur",
    "CHN": "china",
    "AUS": "australia",
    "SAU": "arabia saudi",
    "QAT": "catar",
    "ARE": "emiratos arabes unidos",
    "IND": "india",
    "IDN": "indonesia",
    "THA": "tailandia",
    "VNM": "vietnam",
    "MYS": "malasia",
    "PHL": "filipinas",
    "HKG": "hong kong",
    "IRQ": "irak",
    "IRN": "iran",
    "ISR": "israel",
    "JOR": "jordania",
    "KWT": "kuwait",
    "LBN": "libano",
    "UZB": "uzbekistan",
    "KGZ": "kirguistan",
    "TKM": "turkmenistan",
    "EGY": "egipto",
    "MAR": "marruecos",
    "DZA": "argelia",
    "TUN": "tunez",
    "ZAF": "sudafrica",
    "NGA": "nigeria",
    "GHA": "ghana",
    "KEN": "kenia",
    "UGA": "uganda",
    "RWA": "ruanda",
    "ZMB": "zambia",
    "ZWE": "zimbabue",
    "MOZ": "mozambique",
    "AGO": "angola",
    "CMR": "camerun",
    "CIV": "costa de marfil",
    "SEN": "senegal",
    "MLI": "mali",
    "BFA": "burkina faso",
    "NER": "niger",
    "BEN": "benin",
    "MRT": "mauritania",
    "SLE": "sierra leona",
    "LBR": "liberia",
    "BDI": "burundi",
    "COD": "rd congo",
    "INT": "internacional",
}

# Catálogo ordenado de mayor a menor longitud de caracteres para evitar que
# "bundesliga" corte "2. bundesliga" o "laliga" corte "laliga ea sports"
_CLAVES_LIGAS_ORDENADAS: List[Tuple[str, str]] = sorted(
    [(normalizar_texto(k), v) for k, v in CATALOGO_LIGAS_FLASHSCORE.items()],
    key=lambda x: len(x[0]),
    reverse=True,
)

_KEYWORDS_FEMENINO = (
    "femenin", "women", "(w)", "wsl", "liga f", "nwsl", "swpl", "elitettan",
    "1. wfl", "1. sznl", "frauen", "femminile", "femenil", "damallsvenskan",
    "toppserien", "we league", "a-liga femenina", "b-liga femenina",
)

_KEYWORDS_JUVENIL = (
    "u19", "u20", "u21", "u23", "sub-19", "sub-20", "sub-21", "sub-23",
    "primavera", "youth", "next gen", "next pro", "premier league 2", "diski challenge",
)

_RE_CANDIDATO_FEMENINO = re.compile(
    r"(?:\(w\)|\bwomen\b|\bfemenin[oa]?\b|\bladies\b|\bfem\.|\bfrauen\b|\bfemminile\b|\bfemenil\b)",
    re.IGNORECASE,
)

_RE_CANDIDATO_JUVENIL_O_FILIAL = re.compile(
    r"(?:\bu17\b|\bu18\b|\bu19\b|\bu20\b|\bu21\b|\bu23\b|\bsub[- ]?(?:17|18|19|20|21|23)\b|\bii\b|\bb\b|\byouth\b|\bprimavera\b|\breserves?\b|\bmestalla\b|\bcastilla\b|\bfilial\b)",
    re.IGNORECASE,
)

# Sufijos de fase/grupo que acompañan a las ligas en Flashscore (ej. "Primera Federación - Grupo 1", "Liga 1 - Clausura")
_RE_SUFIJO_FASE_LIGA = re.compile(
    r"^\s*[-–—]\s*(?:grupo\s+[a-z0-9]+|clausura|apertura|fase\s+[a-z0-9]+|etapa\s+[a-z0-9]+|zona\s+[a-z0-9]+|serie\s+[a-z0-9]+|playoffs?|championship\s+group|relegation\s+group|norte|sur|este|oeste)(?=\s+)",
    re.IGNORECASE,
)


def detectar_categoria_desde_texto(texto_liga_o_equipo: str) -> Tuple[bool, bool]:
    """
    Detecta si el usuario pidió fútbol Femenino o Juvenil/Sub desde el texto del comando.
    Devuelve (es_femenino_pedido, es_juvenil_pedido).
    """
    low = (texto_liga_o_equipo or "").lower()
    norm = normalizar_texto(texto_liga_o_equipo)
    combinado = f"{low} {norm}"
    es_femenino_pedido = any(k in combinado for k in _KEYWORDS_FEMENINO)
    es_juvenil_pedido = any(k in combinado for k in _KEYWORDS_JUVENIL)
    return es_femenino_pedido, es_juvenil_pedido


def normalizar_pais_iso(pais_o_liga: Optional[str]) -> str:
    """Convierte un nombre de país, código FIFA o nombre de liga en su código ISO-3 (ej. 'DEU', 'ESP', 'ARG')."""
    if not pais_o_liga:
        return ""
    raw = str(pais_o_liga).strip()
    if raw.upper() in MAPA_ISO3_A_PAIS_INTERNO:
        return raw.upper()
    norm = normalizar_texto(raw)
    if norm in MAPA_PAIS_A_ISO3:
        return MAPA_PAIS_A_ISO3[norm]
    if norm in CATALOGO_LIGAS_FLASHSCORE:
        return CATALOGO_LIGAS_FLASHSCORE[norm]
    for clave_liga, iso in _CLAVES_LIGAS_ORDENADAS:
        if clave_liga == norm or (len(clave_liga) >= 5 and re.search(rf"\b{re.escape(clave_liga)}\b", norm)):
            # Evitar falsos positivos de "serie a" en "serie a brazil" o "serie a ecuador"
            if clave_liga in ("serie a", "serie b"):
                if "bra" in norm:
                    return "BRA"
                if "ecu" in norm or "ligapro" in norm:
                    return "ECU"
            if clave_liga == "premier league":
                if "ukr" in norm or "ucr" in norm:
                    return "UKR"
                if "rus" in norm:
                    return "RUS"
                if "egy" in norm or "egipto" in norm:
                    return "EGY"
            if clave_liga == "liga nacional":
                if "gtm" in norm or "guatemala" in norm:
                    return "GTM"
                return "HND"
            return iso
    if "honduras" in norm or "liga nacional" in norm:
        return "HND"
    if "uruguay" in norm or "liga auf" in norm:
        return "URY"
    if "argentina" in norm or "liga profesional" in norm:
        return "ARG"
    return ""


def extraer_liga_flashscore(texto: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Extrae el prefijo de liga de Flashscore comparando de mayor a menor longitud
    de caracteres y absorbiendo sufijos de grupo/fase (`- Grupo 1`, `- Clausura`, `Femenina`).
    Devuelve `(info_liga_dict | None, resto_texto_sin_liga)`.
    """
    original = (texto or "").strip()
    if not original:
        return None, ""

    # Limpiar posible prefijo de comando (/mercados, /auditar, /h2h)
    trabajo = re.sub(
        r"^/(?:mercados|mercado|probabilidades|analizar_mercados|auditar|auditoria|auditor[ií]a|historial|h2h|vs|comparar|comparativa|duelo|evaluar)(?:@\w+)?\s+",
        "",
        original,
        flags=re.IGNORECASE,
    ).strip()
    trabajo = re.sub(r"^(?:liga|competicion|competici[oó]n|torneo)\s*[:=]\s*", "", trabajo, flags=re.IGNORECASE).strip()

    # 1. Soporte para corchetes/paréntesis al inicio: "[2. Bundesliga] Hamburgo vs Colonia"
    m_bracket = re.match(r"^[\[\(]\s*([^\]\)]+?)\s*[\]\)]\s*[:\-–—]?\s*(.+)$", trabajo)
    if m_bracket:
        posible_liga_raw = m_bracket.group(1).strip()
        posible_norm = normalizar_texto(posible_liga_raw)
        resto_b = m_bracket.group(2).strip()
        for patron_norm, pais_iso in _CLAVES_LIGAS_ORDENADOS_SIN_ALIAS():
            if posible_norm == patron_norm or posible_norm.startswith(patron_norm + " "):
                es_fem, es_juv = detectar_categoria_desde_texto(posible_liga_raw)
                return {
                    "nombre": posible_liga_raw,
                    "liga": posible_liga_raw,
                    "liga_norm": patron_norm,
                    "pais_iso": pais_iso,
                    "pais": MAPA_ISO3_A_PAIS_INTERNO.get(pais_iso, pais_iso.lower()),
                    "es_femenino": es_fem,
                    "es_juvenil": es_juv,
                    "liga_forzada": True,
                }, resto_b

    # 2. Comparar prefijo contra CATALOGO_LIGAS_FLASHSCORE (de mayor a menor longitud)
    # Construimos un patrón regex por cada clave ordenada que permita tildes/puntuación original en `trabajo`
    for patron_norm, pais_iso in _CLAVES_LIGAS_ORDENADAS:
        patron_regex = _construir_regex_prefijo_liga(patron_norm)
        m = patron_regex.match(trabajo)
        if not m:
            continue
        fin_liga = m.end(1)
        resto = trabajo[fin_liga:]

        # Absorber sufijos de fase/grupo ("- Grupo 1", "- Clausura", "- Apertura")
        m_fase = _RE_SUFIJO_FASE_LIGA.match(resto)
        if m_fase:
            fin_liga += m_fase.end(0)
            resto = trabajo[fin_liga:]

        # Absorber modificador de categoría inmediato ("Femenina", "Femenino", "Women", "U21", "Sub-20")
        m_cat = re.match(
            r"^\s+(?:femenin[oa]|women|fem\.|u19|u20|u21|u23|sub-(?:19|20|21|23))(?=\s+\S)",
            resto,
            flags=re.IGNORECASE,
        )
        if m_cat:
            fin_liga += m_cat.end(0)
            resto = trabajo[fin_liga:]

        resto_limpio = resto.strip().strip(":|·").strip()
        # No cortar si el resto quedó vacío o empieza directamente con "vs" (significaría que era el nombre del equipo)
        if not resto_limpio or re.match(r"^(?:vs\.?|v|contra|h2h)\b", resto_limpio, flags=re.IGNORECASE):
            continue

        liga_raw = trabajo[:fin_liga].strip(" :-–—|")
        es_fem, es_juv = detectar_categoria_desde_texto(liga_raw)
        return {
            "nombre": liga_raw,
            "liga": liga_raw,
            "liga_norm": patron_norm,
            "pais_iso": pais_iso,
            "pais": MAPA_ISO3_A_PAIS_INTERNO.get(pais_iso, pais_iso.lower()),
            "es_femenino": es_fem,
            "es_juvenil": es_juv,
            "liga_forzada": True,
        }, resto_limpio

    return None, trabajo


def _CLAVES_LIGAS_ORDENADOS_SIN_ALIAS() -> List[Tuple[str, str]]:
    return _CLAVES_LIGAS_ORDENADAS


_CACHE_REGEX_LIGA: Dict[str, re.Pattern] = {}


def _mapa_char_regex(c: str) -> str:
    mapa = {
        "a": "[aáàäâã]",
        "e": "[eéèëê]",
        "i": "[iíìïî]",
        "o": "[oóòöôõø]",
        "u": "[uúùüû]",
        "n": "[nñń]",
        "c": "[cçčć]",
        "s": "[sšśß]",
        "z": "[zžźż]",
        ".": r"\.?",
        "-": r"[\s\-–—]+",
        " ": r"\s+",
    }
    return mapa.get(c, re.escape(c))


def _construir_regex_prefijo_liga(patron_norm: str) -> re.Pattern:
    if patron_norm in _CACHE_REGEX_LIGA:
        return _CACHE_REGEX_LIGA[patron_norm]
    partes = [_mapa_char_regex(ch) for ch in patron_norm]
    cuerpo = "".join(partes)
    # Debe coincidir al inicio del texto y estar seguido de espacio o separador + resto
    pat = re.compile(rf"^({cuerpo})(?=\s*[:\|\·]?\s+\S)", re.IGNORECASE)
    _CACHE_REGEX_LIGA[patron_norm] = pat
    return pat


def extraer_calificadores_nombre(nombre_equipo: str) -> Tuple[str, Dict[str, Any]]:
    """
    Extrae calificadores geográficos o de entidad de un nombre de club:
      - 'Liverpool M.' -> ('Liverpool', {'calificador': 'montevideo', 'pais_iso': 'URY'})
      - 'Liverpool Montevideo' -> ('Liverpool', {'calificador': 'montevideo', 'pais_iso': 'URY'})
      - 'Juventud de Las Piedras' -> ('Juventud', {'calificador': 'las piedras', 'pais_iso': 'URY'})
      - 'Arsenal de Sarandí' -> ('Arsenal', {'calificador': 'sarandi', 'pais_iso': 'ARG'})
    """
    limpio = str(nombre_equipo or "").strip()
    norm = normalizar_texto(limpio)
    info: Dict[str, Any] = {"calificador": "", "pais_iso": ""}

    if re.search(r"\b(?:m\.|montevideo|de montevideo)\b", norm) or norm.endswith(" m"):
        info["calificador"] = "montevideo"
        info["pais_iso"] = "URY"
        limpio_sin = re.sub(r"\s+(?:m\.|montevideo|de montevideo|m)\b", "", norm).strip()
        return limpio_sin, info
    if re.search(r"\b(?:las piedras|de las piedras)\b", norm):
        info["calificador"] = "las piedras"
        info["pais_iso"] = "URY"
        limpio_sin = re.sub(r"\s+(?:las piedras|de las piedras)\b", "", norm).strip()
        return limpio_sin, info
    if re.search(r"\b(?:sarandi|de sarandi)\b", norm):
        info["calificador"] = "sarandi"
        info["pais_iso"] = "ARG"
        limpio_sin = re.sub(r"\s+(?:sarandi|de sarandi)\b", "", norm).strip()
        return limpio_sin, info
    if re.search(r"\b(?:vina del mar|de vina del mar|vina)\b", norm):
        info["calificador"] = "vina del mar"
        info["pais_iso"] = "CHL"
        return norm, info
    if re.search(r"\b(?:vinto|de vinto)\b", norm):
        info["calificador"] = "vinto"
        info["pais_iso"] = "BOL"
        return norm, info

    return norm, info


def _paises_posibles_para_club(nombre: str) -> set[str]:
    """Determina los posibles países para un club según el diccionario y homónimos."""
    norm = normalizar_texto(nombre)
    norm_sin_siglas = re.sub(r"\b(?:fc|cf|sc|ac|rcd|cd|ud|sd|sv|vfl|vfb|ca|club)\b", " ", norm)
    norm_sin_siglas = re.sub(r"\s+", " ", norm_sin_siglas).strip()

    paises: set[str] = set()
    _, calif = extraer_calificadores_nombre(nombre)
    if calif.get("pais_iso"):
        paises.add(calif["pais_iso"])

    for k in (norm, norm_sin_siglas):
        if k in CLUBES_HOMONIMOS:
            for _, iso in CLUBES_HOMONIMOS[k]:
                paises.add(iso)
        if k in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB:
            paises.add(DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB[k][1])

    return paises


def resolver_equipo_individual(
    nombre_equipo: str,
    pais_iso_contexto: Optional[str] = None,
    es_femenino: bool = False,
    es_juvenil: bool = False,
) -> Tuple[str, str]:
    """
    Traduce un nombre de equipo escrito en español/Flashscore a su `(nombre_oficial_fotmob, pais_iso)`.
    Aplica la Regla Dura Anti-Colisión (`Bayer` != `Bayern`, `CA Platense [ARG]` != `Platense FC [HND]`).
    """
    limpio = (nombre_equipo or "").strip().strip('"').strip("'").strip()
    if not limpio:
        return "", pais_iso_contexto or ""

    norm = normalizar_texto(limpio)
    norm_sin_siglas = re.sub(r"\b(?:fc|cf|sc|ac|rcd|cd|ud|sd|sv|vfl|vfb|ca|club)\b", " ", norm)
    norm_sin_siglas = re.sub(r"\s+", " ", norm_sin_siglas).strip()

    nom_base, calif = extraer_calificadores_nombre(limpio)
    if calif.get("pais_iso") and (not pais_iso_contexto or pais_iso_contexto == "INT"):
        pais_iso_contexto = calif["pais_iso"]

    # Regla dura anti-colisión Bayer vs Bayern
    tokens_norm = set(re.findall(r"[a-z0-9]+", norm))
    if "bayer" in tokens_norm and not ({"bayern", "munich", "munchen"} & tokens_norm):
        return "Bayer 04 Leverkusen", "DEU"
    if ({"bayern", "munich", "munchen"} & tokens_norm) and "leverkusen" not in tokens_norm:
        return "FC Bayern München", "DEU"

    # Verificar tabla CLUBES_HOMONIMOS con país de contexto
    if pais_iso_contexto and pais_iso_contexto not in ("", "INT"):
        for k in (norm, norm_sin_siglas, nom_base):
            if k in CLUBES_HOMONIMOS:
                for nom_cand, iso_cand in CLUBES_HOMONIMOS[k]:
                    if iso_cand == pais_iso_contexto:
                        return nom_cand, iso_cand

    # Buscar en DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB
    for clave_busqueda in (norm, norm_sin_siglas, nom_base):
        if clave_busqueda in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB:
            nom_fotmob, iso_eq = DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB[clave_busqueda]
            if pais_iso_contexto and pais_iso_contexto not in ("", "INT") and iso_eq != pais_iso_contexto:
                continue
            return nom_fotmob, iso_eq

    # Si contiene palabras de un club en DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB del mismo país
    if pais_iso_contexto and pais_iso_contexto not in ("", "INT"):
        mejor_match: Optional[Tuple[str, str]] = None
        mejor_len = 0
        for k_alias, (nom_fot, iso_eq) in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB.items():
            if iso_eq != pais_iso_contexto:
                continue
            if len(k_alias) >= 4 and re.search(rf"\b{re.escape(k_alias)}\b", norm):
                if len(k_alias) > mejor_len:
                    mejor_match = (nom_fot, iso_eq)
                    mejor_len = len(k_alias)
        if mejor_match:
            return mejor_match

    return limpio, (pais_iso_contexto or "")


class ResultadoResolucion(tuple):
    """
    Tupla `(equipo_local_fotmob, equipo_visita_fotmob, pais_iso)` enriquecida con atributos
    y acceso por clave para compatibilidad 100% con desempaquetado de tupla y diccionarios:
      - `loc, vis, pais = resolver(...)`
      - `resolver(...) == ("1. FC Köln", "Bayer 04 Leverkusen", "DEU")`
      - `res.es_femenino`, `res.es_juvenil`, `res.liga`, `res["es_femenino"]`
      - `res.pais_local`, `res.pais_visita`, `res.es_copa`, `res.es_internacional`
      - `res.identidad_verificada`, `res.estado_identidad`, `res.motivo_identidad`
    """
    def __new__(
        cls,
        equipo_local: str,
        equipo_visita: str,
        pais_iso: str,
        liga: str = "",
        es_femenino: bool = False,
        es_juvenil: bool = False,
        equipo_local_raw: str = "",
        equipo_visita_raw: str = "",
        pais_local: str = "",
        pais_visita: str = "",
        es_internacional: bool = False,
        es_copa: bool = False,
        identidad_verificada: bool = True,
        estado_identidad: str = "verificada",
        motivo_identidad: str = "",
        candidatos_local: Optional[List[Dict[str, Any]]] = None,
        candidatos_visita: Optional[List[Dict[str, Any]]] = None,
        criterio_desempate: str = "",
        nivel_confianza: str = "alta",
        id_proveedor_local: Optional[str] = None,
        id_proveedor_visita: Optional[str] = None,
    ):
        inst = super().__new__(cls, (equipo_local, equipo_visita, pais_iso))
        inst.equipo_local = equipo_local
        inst.equipo_visita = equipo_visita
        inst.pais_iso = pais_iso
        inst.liga = liga
        inst.es_femenino = bool(es_femenino)
        inst.es_juvenil = bool(es_juvenil)
        inst.equipo_local_raw = equipo_local_raw or equipo_local
        inst.equipo_visita_raw = equipo_visita_raw or equipo_visita
        inst.pais_local = pais_local or pais_iso
        inst.pais_visita = pais_visita or pais_iso
        inst.es_internacional = bool(es_internacional)
        inst.es_copa = bool(es_copa)
        inst.identidad_verificada = bool(identidad_verificada)
        inst.estado_identidad = estado_identidad
        inst.motivo_identidad = motivo_identidad
        inst.candidatos_local = list(candidatos_local or [])
        inst.candidatos_visita = list(candidatos_visita or [])
        inst.criterio_desempate = criterio_desempate
        inst.nivel_confianza = nivel_confianza
        inst.id_proveedor_local = id_proveedor_local
        inst.id_proveedor_visita = id_proveedor_visita
        return inst

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, str):
            return getattr(self, item)
        return super().__getitem__(item)

    def a_dict(self) -> Dict[str, Any]:
        return {
            "equipo_local": self.equipo_local,
            "equipo_visita": self.equipo_visita,
            "pais_iso": self.pais_iso,
            "pais_local": self.pais_local,
            "pais_visita": self.pais_visita,
            "liga": self.liga,
            "es_femenino": self.es_femenino,
            "es_juvenil": self.es_juvenil,
            "es_internacional": self.es_internacional,
            "es_copa": self.es_copa,
            "equipo_local_raw": self.equipo_local_raw,
            "equipo_visita_raw": self.equipo_visita_raw,
            "identidad_verificada": self.identidad_verificada,
            "estado_identidad": self.estado_identidad,
            "motivo_identidad": self.motivo_identidad,
            "candidatos_local": self.candidatos_local,
            "candidatos_visita": self.candidatos_visita,
            "criterio_desempate": self.criterio_desempate,
            "nivel_confianza": self.nivel_confianza,
            "id_proveedor_local": self.id_proveedor_local,
            "id_proveedor_visita": self.id_proveedor_visita,
        }


def parsear_comando_mercados(texto_comando: str) -> Dict[str, Any]:
    """
    A) SEPARADOR INTELIGENTE DE COMANDO `/mercados [Liga Flashscore] [Equipo Local] vs [Equipo Visita]`.
    Compara el prefijo del texto contra el Catálogo Oficial de Ligas Flashscore
    (ordenado de mayor a menor longitud de caracteres) antes de separar `equipo_local`
    y `equipo_visita` por el delimitador ` vs ` o ` - `.
    """
    if not texto_comando or not str(texto_comando).strip():
        return {
            "liga": "",
            "liga_norm": "",
            "pais_iso": "",
            "pais_local": "",
            "pais_visita": "",
            "equipo_local_raw": "",
            "equipo_visita_raw": "",
            "equipo_local": "",
            "equipo_visita": "",
            "es_femenino": False,
            "es_juvenil": False,
            "es_internacional": False,
            "es_copa": False,
            "identidad_verificada": False,
            "estado_identidad": "identidad_no_verificada",
            "motivo_identidad": "Comando vacío",
            "criterio_desempate": "",
            "nivel_confianza": "nula",
            "id_proveedor_local": None,
            "id_proveedor_visita": None,
        }

    # Limpiar comando inicial y parámetros opcionales (umbral, limite, borrador)
    cuerpo = re.sub(
        r"^/(?:mercados|mercado|probabilidades|analizar_mercados|auditar|auditoria|auditor[ií]a|historial|h2h|vs|comparar|comparativa|duelo)(?:@\w+)?\s*",
        "",
        str(texto_comando).strip(),
        flags=re.IGNORECASE,
    ).strip()
    cuerpo = re.sub(r"\bborrador\b", " ", cuerpo, flags=re.IGNORECASE).strip()
    cuerpo = re.sub(r"\b(?:umbral|threshold|u)\s*[:=]\s*\d+(?:[.,]\d+)?\b", " ", cuerpo, flags=re.IGNORECASE).strip()
    cuerpo = re.sub(r"\b(?:limite|l[ií]mite|minimo|m[ií]nimo|min|n)\s*[:=]\s*\d{1,2}\b", " ", cuerpo, flags=re.IGNORECASE).strip()
    cuerpo = re.sub(r"\s+", " ", cuerpo).strip()

    # 1. Extraer prefijo de liga ANTES de separar equipos (protege ligas con '-' como 'Primera Federación - Grupo 1')
    liga_info, resto_sin_liga = extraer_liga_flashscore(cuerpo)
    trabajo = resto_sin_liga if liga_info else cuerpo

    # 2. Separar por ' vs ' / ' contra ' / ' h2h ' primero; si no existe, por ' - '
    m_vs = re.split(r"\s+(?:vs\.?|v|contra|h2h|x|@)\s+", trabajo, maxsplit=1, flags=re.IGNORECASE)
    if len(m_vs) == 2:
        loc_raw, vis_raw = m_vs[0].strip(), m_vs[1].strip()
    else:
        m_guion = re.split(r"\s+[-–—]\s+", trabajo, maxsplit=1)
        if len(m_guion) == 2:
            loc_raw, vis_raw = m_guion[0].strip(), m_guion[1].strip()
        else:
            loc_raw, vis_raw = trabajo.strip(), ""

    # Si no se detectó liga antes, verificar si loc_raw aún lleva prefijo de liga pegado
    if not liga_info and loc_raw:
        liga_loc, loc_limpio = extraer_liga_flashscore(loc_raw)
        if liga_loc and loc_limpio:
            liga_info = liga_loc
            loc_raw = loc_limpio

    liga_nombre = (liga_info or {}).get("nombre", "")
    liga_norm = (liga_info or {}).get("liga_norm", "")
    pais_iso = (liga_info or {}).get("pais_iso", "")

    es_fem, es_juv = detectar_categoria_desde_texto(f"{liga_nombre} {loc_raw} {vis_raw}")
    res = resolver(liga_nombre, loc_raw, vis_raw)

    return {
        "liga": res.liga or liga_nombre,
        "liga_norm": liga_norm,
        "pais_iso": res.pais_iso or pais_iso,
        "pais_local": res.pais_local,
        "pais_visita": res.pais_visita,
        "equipo_local_raw": loc_raw,
        "equipo_visita_raw": vis_raw,
        "equipo_local": res.equipo_local,
        "equipo_visita": res.equipo_visita,
        "es_femenino": res.es_femenino or es_fem,
        "es_juvenil": res.es_juvenil or es_juv,
        "es_internacional": res.es_internacional,
        "es_copa": res.es_copa,
        "identidad_verificada": res.identidad_verificada,
        "estado_identidad": res.estado_identidad,
        "motivo_identidad": res.motivo_identidad,
        "criterio_desempate": res.criterio_desempate,
        "nivel_confianza": res.nivel_confianza,
        "id_proveedor_local": res.id_proveedor_local,
        "id_proveedor_visita": res.id_proveedor_visita,
    }


def resolver(
    liga_o_comando: str,
    equipo_local: Optional[str] = None,
    equipo_visita: Optional[str] = None,
) -> ResultadoResolucion:
    """
    Resuelve de forma conjunta (Entity Resolution) `(liga, equipo_local, equipo_visita)` o comando
    a `ResultadoResolucion(equipo_local_fotmob, equipo_visita_fotmob, pais_iso)` enriquecida.
    """
    if equipo_local is None and equipo_visita is None:
        parsed = parsear_comando_mercados(liga_o_comando)
        return ResultadoResolucion(
            parsed["equipo_local"],
            parsed["equipo_visita"],
            parsed["pais_iso"],
            liga=parsed["liga"],
            es_femenino=parsed["es_femenino"],
            es_juvenil=parsed["es_juvenil"],
            equipo_local_raw=parsed["equipo_local_raw"],
            equipo_visita_raw=parsed["equipo_visita_raw"],
            pais_local=parsed.get("pais_local", parsed["pais_iso"]),
            pais_visita=parsed.get("pais_visita", parsed["pais_iso"]),
            es_internacional=parsed.get("es_internacional", False),
            es_copa=parsed.get("es_copa", False),
            identidad_verificada=parsed.get("identidad_verificada", True),
            estado_identidad=parsed.get("estado_identidad", "verificada"),
            motivo_identidad=parsed.get("motivo_identidad", ""),
            candidatos_local=parsed.get("candidatos_local", []),
            candidatos_visita=parsed.get("candidatos_visita", []),
            criterio_desempate=parsed.get("criterio_desempate", ""),
            nivel_confianza=parsed.get("nivel_confianza", "alta"),
            id_proveedor_local=parsed.get("id_proveedor_local"),
            id_proveedor_visita=parsed.get("id_proveedor_visita"),
        )

    liga_str = (liga_o_comando or "").strip()
    loc_str = (equipo_local or "").strip()
    vis_str = (equipo_visita or "").strip()

    # Si equipo_local trae accidentalmente la liga pegada, limpiarla
    if not liga_str and loc_str:
        info_l, loc_limpio = extraer_liga_flashscore(loc_str)
        if info_l and loc_limpio:
            liga_str = info_l["nombre"]
            loc_str = loc_limpio
    elif liga_str and loc_str:
        info_l2, loc_limpio2 = extraer_liga_flashscore(loc_str)
        if info_l2 and loc_limpio2:
            loc_str = loc_limpio2

    pais_iso_liga = normalizar_pais_iso(liga_str)
    liga_norm = normalizar_texto(liga_str)
    es_fem, es_juv = detectar_categoria_desde_texto(f"{liga_str} {loc_str} {vis_str}")

    es_internacional = (pais_iso_liga == "INT") or any(
        k in liga_norm
        for k in (
            "champions", "europa league", "conference", "libertadores",
            "sudamericana", "concacaf", "mundial", "international", "friendly", "amistoso"
        )
    )
    es_copa = any(
        k in liga_norm
        for k in ("copa", "cup", "pokal", "beker", "taca", "supercopa", "superliga")
    )

    # Paso conjunto: analizar calificadores y países posibles
    _, calif_l = extraer_calificadores_nombre(loc_str)
    _, calif_v = extraer_calificadores_nombre(vis_str)
    iso_calif_l = calif_l.get("pais_iso")
    iso_calif_v = calif_v.get("pais_iso")

    cands_paises_l = _paises_posibles_para_club(loc_str)
    cands_paises_v = _paises_posibles_para_club(vis_str)

    criterio_desempate = ""
    pais_conjunto = ""

    if not es_internacional:
        if pais_iso_liga and pais_iso_liga != "INT":
            pais_conjunto = pais_iso_liga
            criterio_desempate = f"liga_declarada ({pais_iso_liga})"
        elif iso_calif_l:
            pais_conjunto = iso_calif_l
            criterio_desempate = f"calificador_local ({loc_str})"
        elif iso_calif_v:
            pais_conjunto = iso_calif_v
            criterio_desempate = f"calificador_visita ({vis_str})"
        else:
            inter = cands_paises_l & cands_paises_v
            if inter:
                pais_conjunto = sorted(inter)[0]
                criterio_desempate = f"interseccion_cruzada_rival ({pais_conjunto})"
            elif len(cands_paises_l) == 1:
                pais_conjunto = list(cands_paises_l)[0]
                criterio_desempate = f"pais_unico_local ({pais_conjunto})"
            elif len(cands_paises_v) == 1:
                pais_conjunto = list(cands_paises_v)[0]
                criterio_desempate = f"pais_unico_visita ({pais_conjunto})"
            else:
                pais_conjunto = ""
                criterio_desempate = "sin_desempate_claro"

        ctx_loc = pais_conjunto or None
        ctx_vis = pais_conjunto or None
        nom_loc, iso_loc = resolver_equipo_individual(loc_str, pais_iso_contexto=ctx_loc, es_femenino=es_fem, es_juvenil=es_juv)
        nom_vis, iso_vis = resolver_equipo_individual(vis_str, pais_iso_contexto=ctx_vis, es_femenino=es_fem, es_juvenil=es_juv)
        pais_final = pais_conjunto or iso_loc or iso_vis or ""
        pais_local = iso_loc or pais_final
        pais_visita = iso_vis or pais_final
    else:
        criterio_desempate = "cruce_internacional"
        nom_loc, iso_loc = resolver_equipo_individual(loc_str, es_femenino=es_fem, es_juvenil=es_juv)
        nom_vis, iso_vis = resolver_equipo_individual(vis_str, es_femenino=es_fem, es_juvenil=es_juv)
        pais_local = iso_loc or ""
        pais_visita = iso_vis or ""
        pais_final = "INT"

    # Verificar si la identidad está confirmada o no verificada
    cand_l_known = bool((nom_loc, pais_local) in FOTMOB_IDS_CONOCIDOS or normalizar_texto(nom_loc) in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB or iso_loc)
    cand_v_known = bool((nom_vis, pais_visita) in FOTMOB_IDS_CONOCIDOS or normalizar_texto(nom_vis) in DICCIONARIO_EQUIPOS_FLASHSCORE_A_FOTMOB or iso_vis)

    if (not cand_l_known and not iso_loc) or (not cand_v_known and not iso_vis):
        identidad_verificada = False
        estado_identidad = "identidad_no_verificada"
        motivo_identidad = f"No se pudo verificar la identidad fehaciente de: {loc_str if (not cand_l_known and not iso_loc) else vis_str}"
        nivel_confianza = "nula"
    else:
        identidad_verificada = True
        estado_identidad = "verificada"
        motivo_identidad = ""
        nivel_confianza = "alta" if (criterio_desempate != "sin_desempate_claro") else "media"

    id_prov_l = FOTMOB_IDS_CONOCIDOS.get((nom_loc, pais_local))
    id_prov_v = FOTMOB_IDS_CONOCIDOS.get((nom_vis, pais_visita))

    return ResultadoResolucion(
        nom_loc,
        nom_vis,
        pais_final,
        liga=liga_str,
        es_femenino=es_fem,
        es_juvenil=es_juv,
        equipo_local_raw=loc_str,
        equipo_visita_raw=vis_str,
        pais_local=pais_local,
        pais_visita=pais_visita,
        es_internacional=es_internacional,
        es_copa=es_copa,
        identidad_verificada=identidad_verificada,
        estado_identidad=estado_identidad,
        motivo_identidad=motivo_identidad,
        candidatos_local=[{"nombre": nom_loc, "pais_iso": pais_local, "id": id_prov_l}],
        candidatos_visita=[{"nombre": nom_vis, "pais_iso": pais_visita, "id": id_prov_v}],
        criterio_desempate=criterio_desempate,
        nivel_confianza=nivel_confianza,
        id_proveedor_local=id_prov_l,
        id_proveedor_visita=id_prov_v,
    )


def validar_y_puntuar_candidato_fotmob(
    candidato: Dict[str, Any],
    texto_buscado: str,
    texto_liga_o_contexto: str = "",
    pais_iso_esperado: Optional[str] = None,
    es_femenino_pedido: Optional[bool] = None,
    es_juvenil_pedido: Optional[bool] = None,
) -> float:
    """
    D) REGLA DURA DE DESAMBIGUACIÓN Y FILTRO CONTEXTUAL DE CATEGORÍA (FEMENINO / JUVENIL / PAÍS).
    Devuelve una puntuación numérica para un candidato de FotMob (descarta con `-9999`
    cualquier candidato inválido por categoría, colisión Bayer/Bayern o país incompatible).
    """
    if not isinstance(candidato, dict):
        return -9999.0

    nombre_cand = str(candidato.get("name") or candidato.get("nombre") or "").strip()
    liga_cand = str(candidato.get("leagueName") or candidato.get("primaryLeagueName") or candidato.get("liga") or "").strip()
    ccode_cand = str(candidato.get("ccode") or candidato.get("country") or candidato.get("country_code") or candidato.get("pais") or "").strip()
    if not nombre_cand:
        return -9999.0

    texto_combinado_pedido = f"{texto_liga_o_contexto or ''} {texto_buscado or ''}".strip()
    fem_auto, juv_auto = detectar_categoria_desde_texto(texto_combinado_pedido)
    es_fem = fem_auto if es_femenino_pedido is None else bool(es_femenino_pedido)
    es_juv = juv_auto if es_juvenil_pedido is None else bool(es_juvenil_pedido)

    cand_es_fem = bool(
        _RE_CANDIDATO_FEMENINO.search(nombre_cand)
        or _RE_CANDIDATO_FEMENINO.search(liga_cand)
        or any(k in liga_cand.lower() for k in ("wsl", "liga f", "nwsl", "swpl", "elitettan", "1. wfl", "1. sznl", "frauen"))
    )
    cand_es_juv = bool(
        _RE_CANDIDATO_JUVENIL_O_FILIAL.search(nombre_cand)
        or any(k in liga_cand.lower() for k in ("u19", "u20", "u21", "u23", "youth", "primavera", "premier league 2", "next pro", "next gen", "diski challenge"))
    )

    # 1. Regla Dura de Categoría (Femenino / Juvenil / Filial)
    if not es_fem and cand_es_fem:
        return -9999.0
    if not es_juv and cand_es_juv:
        return -9999.0

    puntos = 0.0
    if es_fem:
        if cand_es_fem:
            puntos += 50.0
        else:
            puntos -= 40.0
    if es_juv:
        if cand_es_juv:
            puntos += 50.0
        else:
            puntos -= 40.0

    # 2. Prohibición estricta Bayer vs Bayern
    norm_buscado = normalizar_texto(texto_buscado)
    norm_cand = normalizar_texto(nombre_cand)
    tokens_buscados = set(re.findall(r"[a-z0-9]+", norm_buscado))
    tokens_cand = set(re.findall(r"[a-z0-9]+", norm_cand))

    if "bayer" in tokens_buscados and not ({"bayern", "munich", "munchen"} & tokens_buscados):
        if "bayern" in tokens_cand or "munchen" in tokens_cand or "munich" in tokens_cand:
            return -9999.0
    if ({"bayern", "munich", "munchen"} & tokens_buscados) and "leverkusen" not in tokens_buscados:
        if "leverkusen" in tokens_cand:
            return -9999.0

    # 3. Filtro por Código de País (pais_iso) y Coherencia Nacional
    iso_esperado = normalizar_pais_iso(pais_iso_esperado or texto_liga_o_contexto)
    iso_cand = normalizar_pais_iso(ccode_cand) or normalizar_pais_iso(liga_cand)
    # También inferir país del candidato si coincide con un club o liga de otro país conocido
    liga_cand_low = normalizar_texto(liga_cand)
    if not iso_cand:
        if "honduras" in liga_cand_low or "platense fc" == norm_cand:
            iso_cand = "HND"
        elif "uruguay" in liga_cand_low or "plaza colonia" in norm_cand or "colonia" == norm_cand:
            iso_cand = "URY"
        elif any(k in liga_cand_low for k in ("lpf", "panama", "tauro", "plaza amador", "veraguas")) or norm_cand == "cd universitario":
            iso_cand = "PAN"
        elif norm_cand == "universitario de deportes":
            iso_cand = "PER"
        elif norm_cand == "tecnico universitario":
            iso_cand = "ECU"
        elif "vinto" in tokens_cand or any(k in liga_cand_low for k in ("bolivia", "division profesional", "copa pacena", "simon bolivar")):
            iso_cand = "BOL"

    # Veto absoluto de falsos homónimos de Universitario (CD Universitario [PAN], Universitario de Deportes [PER], Técnico Universitario [ECU])
    if "vinto" in tokens_buscados or iso_esperado == "BOL":
        if norm_cand in ("cd universitario", "universitario de deportes", "tecnico universitario") or (
            "universitario" in tokens_cand and "vinto" not in tokens_cand and iso_cand != "BOL"
        ):
            return -99999.0

    # Veto general cuando la consulta tiene un apellido/ciudad distintivo y el candidato solo coincide en una raíz genérica
    _RAICES_GENERICAS = {
        "universitario", "universidad", "real", "nacional", "deportivo", "atletico",
        "club", "sporting", "union", "independiente", "san", "defensor", "alianza",
        "fc", "cd", "ca", "cf", "sc", "de", "del", "la", "el",
    }
    apellidos_distintivos = tokens_buscados - _RAICES_GENERICAS
    if apellidos_distintivos and not (apellidos_distintivos & tokens_cand):
        if not (iso_esperado and iso_cand == iso_esperado):
            return -99999.0

    if iso_esperado and iso_esperado != "INT":
        if iso_cand:
            if iso_cand == iso_esperado:
                puntos += 100.0
            else:
                # Descartar clubes homónimos de otros países cuando se pide una liga nacional
                return -9999.0

    # 4. Similitud con el nombre oficial traducido de FotMob
    nom_oficial_esperado, iso_oficial = resolver_equipo_individual(
        texto_buscado,
        pais_iso_contexto=iso_esperado,
        es_femenino=es_fem,
        es_juvenil=es_juv,
    )
    norm_oficial = normalizar_texto(nom_oficial_esperado)
    # Limpiar sufijos femeninos/juveniles para comparar la raíz del nombre del club
    norm_cand_raiz = re.sub(r"\s*\((?:w|women)\)\s*|\b(?:women|femenino|femenina|ladies)\b", "", norm_cand).strip()

    if norm_cand == norm_oficial or norm_cand_raiz == norm_oficial:
        puntos += 120.0
    elif norm_oficial and (norm_oficial in norm_cand_raiz or norm_cand_raiz in norm_oficial):
        puntos += 85.0
    else:
        ratio = max(
            SequenceMatcher(None, norm_oficial, norm_cand_raiz).ratio() if norm_oficial else 0.0,
            SequenceMatcher(None, norm_buscado, norm_cand_raiz).ratio() if norm_buscado else 0.0,
        )
        if ratio < 0.45:
            return -9999.0
        puntos += ratio * 60.0

    score_prov = float(candidato.get("score") or 0.0)
    puntos += min(score_prov / 1000.0, 15.0)
    return round(puntos, 3)


def construir_clave_cache_entidad(
    team_id_o_nombre: Any,
    pais_iso: str = "",
    es_femenino: bool = False,
    es_juvenil: bool = False,
    competicion: str = "",
    proveedor: str = "fotmob",
) -> str:
    """
    Construye la clave canónica de caché con prefijo del proveedor:
        f"{proveedor}_{team_id}_{pais_iso}_{es_femenino}_{es_juvenil}"
    """
    prov = str(proveedor or "fotmob").strip().lower()
    if prov not in ("fotmob", "flashscore"):
        prov = "fotmob"
    tid = str(team_id_o_nombre or "").strip()
    iso = normalizar_pais_iso(pais_iso) or "UNK"
    if not tid.isdigit():
        nom_of, iso_of = resolver_equipo_individual(tid, pais_iso_contexto=iso if iso != "UNK" else None, es_femenino=es_femenino, es_juvenil=es_juvenil)
        if iso == "UNK" and iso_of:
            iso = iso_of
        tid_conocido = FOTMOB_IDS_CONOCIDOS.get((nom_of, iso))
        if tid_conocido:
            tid = tid_conocido
        else:
            tid = re.sub(r"[^a-z0-9]+", "_", normalizar_texto(nom_of or tid)).strip("_") or "equipo"
    comp_slug = ""
    if competicion and str(competicion).strip():
        comp_norm = normalizar_texto(competicion)
        if any(k in comp_norm for k in ("copa", "cup", "pokal", "beker", "taca", "champions", "libertadores", "sudamericana")):
            comp_slug = f"_{re.sub(r'[^a-z0-9]+', '_', comp_norm).strip('_')}"
    return f"{prov}_{tid}_{iso}_{bool(es_femenino)}_{bool(es_juvenil)}{comp_slug}"


def formatear_mensaje_entidades_verificadas(
    nombre_local: str,
    pais_local: str,
    nombre_visita: str,
    pais_visita: str,
    liga: str = "",
) -> str:
    """
    Genera el mensaje oficial de verificación de entidades para Telegram:
    ✅ Entidades verificadas: 1. FC Köln [DEU] vs Bayer 04 Leverkusen [DEU] (Liga: Bundesliga).
    ✅ Entidades verificadas: Real Madrid [ESP] vs FC Bayern München [DEU] (Liga: Champions League).
    """
    _, iso_eq_l = resolver_equipo_individual(nombre_local)
    _, iso_eq_v = resolver_equipo_individual(nombre_visita)
    iso_liga = normalizar_pais_iso(liga)
    es_int = (iso_liga == "INT") or any(
        k in normalizar_texto(liga)
        for k in ("champions", "europa league", "conference", "libertadores", "sudamericana", "concacaf", "mundial", "international")
    )

    if es_int:
        iso_l = normalizar_pais_iso(pais_local) or iso_eq_l or "INT"
        iso_v = normalizar_pais_iso(pais_visita) or iso_eq_v or "INT"
    else:
        iso_l_norm = normalizar_pais_iso(pais_local)
        iso_v_norm = normalizar_pais_iso(pais_visita)
        iso_l = (
            (iso_l_norm if iso_l_norm != "INT" else "")
            or (iso_liga if iso_liga != "INT" else "")
            or iso_eq_l
            or iso_eq_v
            or iso_l_norm
            or "INT"
        )
        iso_v = (
            (iso_v_norm if iso_v_norm != "INT" else "")
            or (iso_liga if iso_liga != "INT" else "")
            or iso_eq_v
            or iso_l
        )
    liga_txt = liga.strip() if liga and str(liga).strip() else "Oficial"
    return f"✅ Entidades verificadas: {nombre_local} [{iso_l}] vs {nombre_visita} [{iso_v}] (Liga: {liga_txt})."
