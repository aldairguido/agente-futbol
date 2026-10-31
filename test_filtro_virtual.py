"""
test_filtro_virtual.py
───────────────────────────────────────────────────────────────────────────────
Prueba Unitaria de Exclusión Estricta de Partidos Virtuales / eFootball:
Valida que la función es_evento_virtual descarte automáticamente:
  • 'Volta'
  • 'eFootball'
  • '6 min de juego'
  • 'Agent', 'Storm', 'Dusk', 'Stasyan', 'Groma'
  • Ligas sintéticas, Cyber, SRL, etc.
Y que NO descarte partidos de fútbol real (ni el club real 'Volta Redonda').
───────────────────────────────────────────────────────────────────────────────
"""

import unittest
import betsson


class TestFiltroVirtual(unittest.TestCase):

    def test_exclusion_efootball_y_duracion(self):
        evento = {
            "local": "Barcelona",
            "visitante": "Real Madrid",
            "liga": "eFootball - 6 min de juego",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_volta(self):
        evento = {
            "local": "Arsenal (Volta)",
            "visitante": "Chelsea (Volta)",
            "liga": "eFootball Volta League",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_jugadores_esports_agent_storm(self):
        evento = {
            "local": "Liverpool (Agent)",
            "visitante": "Manchester City (Storm)",
            "liga": "Cyber Live Arena",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_dusk_stasyan(self):
        evento = {
            "local": "Juventus (Dusk)",
            "visitante": "Milan (Stasyan)",
            "liga": "GT League",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_groma(self):
        evento = {
            "local": "Bayern München (Groma)",
            "visitante": "Borussia Dortmund (Agent)",
            "liga": "H2H Liga Pro",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_string_directo(self):
        texto = "eFootball - 6 min de juego: Agent vs Storm"
        self.assertTrue(betsson.es_evento_virtual(texto))

    def test_exclusion_simulated_reality(self):
        evento = {
            "local": "Inter SRL",
            "visitante": "Roma SRL",
            "liga": "Simulated Reality League",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_no_exclusion_futbol_real(self):
        evento = {
            "local": "Torino",
            "visitante": "Udinese",
            "liga": "Italia Serie A",
            "slug": "futbol/italia/serie-a/torino-udinese",
        }
        self.assertFalse(betsson.es_evento_virtual(evento))

    def test_no_exclusion_peru_liga1(self):
        evento = {
            "local": "Sport Boys",
            "visitante": "Alianza Atlético",
            "liga": "Perú Liga 1",
            "slug": "futbol/peru/peru-liga-1/sport-boys-alianza-atletico",
        }
        self.assertFalse(betsson.es_evento_virtual(evento))

    def test_no_exclusion_volta_redonda_real(self):
        # El club brasileño Volta Redonda no debe descartarse si es fútbol real
        evento = {
            "local": "Volta Redonda",
            "visitante": "Botafogo SP",
            "liga": "Brasil Serie C",
            "slug": "futbol/brasil/serie-c/volta-redonda-botafogo-sp",
        }
        self.assertFalse(betsson.es_evento_virtual(evento))


    def test_exclusion_battling_league(self):
        evento = {
            "local": "Real Madrid",
            "visitante": "Barcelona",
            "liga": "Battling Champions League",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_valkyrie(self):
        evento = {
            "local": "Arsenal (Valkyrie)",
            "visitante": "Chelsea",
            "liga": "Cyber Esports",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_adratic(self):
        evento = {
            "local": "Dinamo Zagreb",
            "visitante": "Hajduk Split",
            "liga": "Adratic League - eFootball",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))

    def test_exclusion_minute_format(self):
        evento = {
            "local": "Liverpool",
            "visitante": "Manchester City",
            "liga": "GT Sports - 8 Minute Match",
        }
        self.assertTrue(betsson.es_evento_virtual(evento))


if __name__ == "__main__":
    unittest.main()
