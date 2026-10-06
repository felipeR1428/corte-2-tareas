"""test_protocolo.py - Pruebas unitarias de comun/protocolo.py (sin red, sin Docker, sin ESP32).

Por qué estas pruebas importan: TODOS los contenedores (players, robots, admin), el emulador de ESP32
y el firmware hablan con el mismo formato de una línea de texto. Si el parser acepta algo que no
debe (un dir de 500, un robot que no existe) o se cae con basura, el error aparece en un contenedor
cualquiera a mitad de la demo. Aquí se comprueba de una vez:

  1. Los cuatro formatos (CTRL, JOINTS, HB/PING/PONG) se arman y se leen de vuelta igual (ida y vuelta).
  2. Los límites: dir/vel se recortan a -100..100, el id de jugador solo 1..3, el robot solo
     spot/pepper/nao, el botón solo 0/1.
  3. Basura: datagramas cortados, con campos de más, con letras donde van números, con bytes que no
     son ASCII... `leer` debe devolver None y NUNCA lanzar excepción (un contenedor no se puede caer
     porque alguien mandó cualquier cosa al puerto UDP).
  4. ContadorSecuencia: pérdidas (saltos de seq), desorden (seq viejo), repetidos y reinicio del emisor.
  5. Jitter (RFC 3550): un caso calculado a mano, paso a paso, comparado contra la clase.

Los casos cuyo nombre empieza por test_defecto_ son errores que estas pruebas encontraron en comun/
y que ya están corregidos (el docstring cuenta el caso exacto); se dejan para que no vuelvan.

Uso (desde la carpeta del tema):
    entorno\\Scripts\\python.exe -m unittest pruebas.test_protocolo -v
    (o, desde pruebas\\:  ..\\entorno\\Scripts\\python.exe -m unittest test_protocolo -v)
"""

import math
import os
import sys
import unittest

# comun/ está en la carpeta del tema (un nivel arriba de pruebas/): se agrega al path para poder
# importar "from comun import protocolo" sin instalar nada, se corra desde donde se corra.
TEMA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TEMA not in sys.path:
    sys.path.insert(0, TEMA)

from comun import protocolo as p  # noqa: E402


# ---------------------------------------------------------------------------------------------
# 1 y 2. Formatos e ida y vuelta, límites
# ---------------------------------------------------------------------------------------------
class TestCtrl(unittest.TestCase):
    """CTRL,<id 1-3>,<seq>,<t_ms>,<dir -100..100>,<vel -100..100>,<boton 0/1>  (ESP32 gamer -> player)."""

    def test_armar_formato_exacto(self):
        # El firmware arma la misma cadena con snprintf("CTRL,%d,%lu,%lu,%d,%d,%d"): si el Python
        # cambiara un espacio o el orden, el player dejaría de entender al ESP32 real.
        self.assertEqual(p.armar_ctrl(2, 15, 123456, -40, 75, 1), "CTRL,2,15,123456,-40,75,1")

    def test_ida_y_vuelta(self):
        m = p.leer(p.armar_ctrl(3, 7, 999, 10, -20, 0))
        self.assertIsInstance(m, p.Ctrl)
        self.assertEqual((m.jugador, m.seq, m.t_ms, m.dir, m.vel, m.boton), (3, 7, 999, 10, -20, 0))

    def test_lee_bytes_y_quita_salto_de_linea(self):
        # Por UDP llega bytes, y el ESP32 puede terminar la línea con \r\n como en el monitor serie.
        m = p.leer(b"CTRL,1,0,10,0,100,0\r\n")
        self.assertEqual((m.jugador, m.vel), (1, 100))

    def test_armar_recorta_a_limites(self):
        # Un joystick mal calibrado puede dar 130: se manda 100, nunca algo fuera de rango.
        self.assertEqual(p.armar_ctrl(1, 0, 0, 250, -999), "CTRL,1,0,0,100,-100,0")

    def test_leer_recorta_a_limites(self):
        # Y si un emisor ajeno manda fuera de rango, el receptor también lo recorta (no confía).
        m = p.leer("CTRL,1,0,0,150,-101,1")
        self.assertEqual((m.dir, m.vel), (100, -100))

    def test_limites_exactos_se_respetan(self):
        m = p.leer("CTRL,1,0,0,-100,100,0")
        self.assertEqual((m.dir, m.vel), (-100, 100))

    def test_jugador_fuera_de_1_a_3_se_ignora(self):
        for jug in ("0", "4", "-1", "99"):
            with self.subTest(jugador=jug):
                self.assertIsNone(p.leer(f"CTRL,{jug},0,0,0,0,0"))

    def test_boton_solo_uno_es_apretado(self):
        # Cualquier cosa distinta de "1" cuenta como suelto (más seguro: no dispara el turbo por error).
        self.assertEqual(p.leer("CTRL,1,0,0,0,0,1").boton, 1)
        for b in ("0", "2", "x", ""):
            with self.subTest(boton=b):
                self.assertEqual(p.leer(f"CTRL,1,0,0,0,0,{b}").boton, 0)

    def test_boton_verdadero_al_armar(self):
        self.assertTrue(p.armar_ctrl(1, 0, 0, 0, 0, True).endswith(",1"))
        self.assertTrue(p.armar_ctrl(1, 0, 0, 0, 0, 5).endswith(",1"))

    def test_decimales_no_se_aceptan_en_ctrl(self):
        # dir/vel son enteros en el contrato; "1.5" no es un CTRL válido.
        self.assertIsNone(p.leer("CTRL,1,0,0,1.5,0,0"))

    def test_armar_acepta_flotantes_y_trunca(self):
        self.assertEqual(p.armar_ctrl(1, 0, 0, 12.9, -3.7), "CTRL,1,0,0,12,-3,0")


class TestJoints(unittest.TestCase):
    """JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton>  (ESP32 robot -> sim, real-to-sim)."""

    def test_armar_formato_un_decimal(self):
        # Un decimal basta (los potenciómetros no dan más precisión) y acorta el datagrama.
        self.assertEqual(p.armar_joints("nao", 3, 50, 12.345, -45, 0, 1),
                         "JOINTS,nao,3,50,12.3,-45.0,0.0,1")

    def test_j3_por_defecto_cero(self):
        self.assertEqual(p.armar_joints("spot", 0, 0, 1, 2), "JOINTS,spot,0,0,1.0,2.0,0.0,0")

    def test_ida_y_vuelta_los_tres_robots(self):
        for robot in p.ROBOTS:
            with self.subTest(robot=robot):
                m = p.leer(p.armar_joints(robot, 42, 1000, 10.5, -20.0, 30.25, 0))
                self.assertIsInstance(m, p.Joints)
                self.assertEqual(m.robot, robot)
                self.assertEqual(m.seq, 42)
                self.assertEqual(m.j, (10.5, -20.0, 30.2))  # 30.25 -> "30.2" (redondeo de :.1f, al par)

    def test_robot_desconocido_o_en_mayusculas(self):
        for robot in ("atlas", "SPOT", "Nao", ""):
            with self.subTest(robot=robot):
                self.assertIsNone(p.leer(f"JOINTS,{robot},0,0,1,2,3,0"))

    def test_campos_de_menos_o_de_mas(self):
        self.assertIsNone(p.leer("JOINTS,spot,0,0,1,2,0"))        # falta j3 (7 campos)
        self.assertIsNone(p.leer("JOINTS,spot,0,0,1,2,3,0,9"))    # uno de más (9 campos)

    def test_angulos_enteros_tambien_valen(self):
        m = p.leer("JOINTS,pepper,1,2,90,-90,0,0")
        self.assertEqual(m.j, (90.0, -90.0, 0.0))


class TestLatidos(unittest.TestCase):
    """HB / PING / PONG,<origen>,<seq>,<t_ms>."""

    def test_los_tres_tipos(self):
        for tipo in ("HB", "PING", "PONG"):
            with self.subTest(tipo=tipo):
                linea = p.armar_latido(tipo, "ctrl-spot", 9, 12345)
                self.assertEqual(linea, f"{tipo},ctrl-spot,9,12345")
                m = p.leer(linea)
                self.assertEqual((m.tipo, m.origen, m.seq, m.t_ms), (tipo, "ctrl-spot", 9, 12345))

    def test_origen_con_guion_y_numeros(self):
        self.assertEqual(p.leer("HB,player-3,0,0").origen, "player-3")

    def test_tipo_desconocido_o_minusculas(self):
        self.assertIsNone(p.leer("hb,player-1,0,0"))
        self.assertIsNone(p.leer("PANG,player-1,0,0"))

    def test_campos_mal(self):
        self.assertIsNone(p.leer("HB,player-1,0"))           # falta t_ms
        self.assertIsNone(p.leer("HB,player-1,0,0,0"))       # sobra uno
        self.assertIsNone(p.leer("HB,player-1,uno,0"))       # seq no numérico


# ---------------------------------------------------------------------------------------------
# 3. Basura: nunca excepción, siempre None
# ---------------------------------------------------------------------------------------------
class TestBasura(unittest.TestCase):
    BASURA = [
        "", " ", "\n", ",,,,,,", "CTRL", "CTRL,", "CTRL,1,2,3",               # vacíos y cortados
        "CTRL,a,b,c,d,e,f", "CTRL,1,0,0,0,0", "CTRL,1,0,0,0,0,0,0",          # tipos y cantidad mal
        "JOINTS,nao,x,0,1,2,3,0", "JOINTS,nao,0,0,uno,2,3,0",
        "GET / HTTP/1.1", "hola mundo", "{\"tipo\":\"control\"}",           # otro protocolo
        b"\xff\xfe\x00CTRL", b"CTRL,1,0,0,0,0,\xe9",                        # bytes no ASCII
        b"\x00" * 64, bytearray(b"HB,x"),
        "CTRL,1,0,0,0,0,0" * 3,                                             # pegados sin salto
    ]

    def test_basura_devuelve_none_sin_excepcion(self):
        for dato in self.BASURA:
            with self.subTest(dato=dato):
                try:
                    r = p.leer(dato)
                except Exception as e:  # pragma: no cover - si pasa es un defecto
                    self.fail(f"leer({dato!r}) lanzó {type(e).__name__}: {e}")
                self.assertIsNone(r)

    def test_datagrama_maximo_udp_de_basura(self):
        # Un datagrama enorme (lo máximo que entra en un UDP) tampoco debe tumbar a nadie.
        self.assertIsNone(p.leer(b"A" * 65507))

    def test_defecto_leer_con_tipo_que_no_es_texto(self):
        """DEFECTO ya corregido (comun/protocolo.py, leer): la doc dice "Nunca lanza excepción", pero si se le
        pasa algo que no es str/bytes (None, un int) hace datos.strip() y lanza AttributeError,
        que no está en el except (ValueError, UnicodeDecodeError, IndexError). Con UDP siempre llega
        bytes, así que en la práctica no se da; pero un receptor que pase el resultado de otra
        función (p. ej. un recv que devolvió None) se cae. Arreglo sugerido: agregar
        AttributeError/TypeError al except."""
        self.assertIsNone(p.leer(None))
        self.assertIsNone(p.leer(123))

    def test_defecto_joints_acepta_nan_e_infinito(self):
        """DEFECTO ya corregido (comun/protocolo.py, leer JOINTS): float("nan") y float("inf") son válidos en
        Python, así que 'JOINTS,nao,0,0,nan,inf,-inf,0' se acepta y llegaría a PyBullet como
        ángulo objetivo (NaN en setJointMotorControl2 puede dejar el robot en una pose inválida o
        "explotar" la simulación). Arreglo sugerido: rechazar si not math.isfinite(j)."""
        m = p.leer("JOINTS,nao,0,0,nan,inf,-inf,0")
        self.assertTrue(m is None or all(math.isfinite(x) for x in m.j))


# ---------------------------------------------------------------------------------------------
# 4. ContadorSecuencia
# ---------------------------------------------------------------------------------------------
class TestContadorSecuencia(unittest.TestCase):

    def contar(self, seqs):
        c = p.ContadorSecuencia()
        for s in seqs:
            c.registrar(s)
        return c

    def test_sin_perdidas(self):
        c = self.contar(range(100))
        self.assertEqual((c.recibidos, c.perdidos, c.desordenados, c.reinicios), (100, 0, 0, 0))
        self.assertEqual(c.perdida_pct(), 0.0)

    def test_vacio(self):
        self.assertEqual(p.ContadorSecuencia().perdida_pct(), 0.0)

    def test_primer_seq_no_es_cero(self):
        # El receptor puede arrancar cuando el emisor ya iba en 500: eso NO son 500 perdidos.
        c = self.contar([500, 501, 502])
        self.assertEqual(c.perdidos, 0)

    def test_perdidas_por_salto(self):
        # Llegan 0,1 y después 5: se perdieron 2,3,4 -> 3 perdidos de 6 enviados = 50 %.
        c = self.contar([0, 1, 5])
        self.assertEqual((c.recibidos, c.perdidos), (3, 3))
        self.assertAlmostEqual(c.perdida_pct(), 50.0)

    def test_perdidas_uno_de_cada_diez(self):
        # 0..99 sin los múltiplos de 10 (salvo el 0): faltan 10,20,...,90 = 9 de 100 -> 9 %.
        seqs = [s for s in range(100) if s == 0 or s % 10]
        c = self.contar(seqs)
        self.assertEqual(c.perdidos, 9)
        self.assertAlmostEqual(c.perdida_pct(), 9.0)

    def test_desorden(self):
        # 10,11,13,12: al llegar 13 se cuenta el 12 como perdido; el 12 llega tarde -> desordenado.
        # (El contador NO descuenta ese perdido: decisión documentada en comun/, en LAN casi no pasa.)
        c = self.contar([10, 11, 13, 12])
        self.assertEqual((c.perdidos, c.desordenados, c.reinicios), (1, 1, 0))
        self.assertEqual(c.ultimo, 13)  # el seq viejo no hace retroceder el último

    def test_repetido(self):
        c = self.contar([10, 11, 11, 12])
        self.assertEqual((c.perdidos, c.desordenados), (0, 1))

    def test_reinicio_del_emisor_vuelve_a_cero(self):
        # El ESP32 se reinicia (botón EN) y su seq vuelve a 0: es un reinicio, no desorden.
        c = self.contar([100, 101, 102, 0, 1, 2, 3])
        self.assertEqual((c.reinicios, c.desordenados, c.perdidos), (1, 0, 0))
        self.assertEqual(c.ultimo, 3)

    def test_reinicio_por_salto_grande_hacia_atras(self):
        # Salto atrás de más de 1000: también reinicio (se perdieron los primeros tras reiniciar).
        c = self.contar([5000, 5001, 3000, 3001])
        self.assertEqual((c.reinicios, c.desordenados), (1, 0))

    def test_defecto_reinicio_perdiendo_los_primeros(self):
        """DEFECTO ya corregido (comun/protocolo.py, ContadorSecuencia.registrar): si el emisor se reinicia
        cuando iba en un seq < 1000 (p. ej. 500, unos 25 s a 20 Hz) y se pierden sus primeros
        3 datagramas (muy probable: el contenedor arranca antes que su ruta o el ESP32 antes que el
        WiFi), el primero que llega es seq=3. Como 3 > 2 y 3 > 500-1000, cae en "desordenado" y
        `ultimo` se queda en 500. A partir de ahí TODOS los paquetes 4,5,...,500 se cuentan como
        desordenados hasta alcanzar el seq viejo, y las pérdidas reales de ese tramo no se ven.
        Caso exacto: registrar 0..500, luego 3,4,5 -> esperado reinicios=1, desordenados=0;
        obtenido reinicios=0, desordenados=3. Arreglo sugerido: tratar como reinicio cualquier
        retroceso mayor que una ventana corta (p. ej. seq < ultimo - 50), o resincronizar tras
        N desordenados seguidos."""
        c = self.contar(list(range(501)) + [3, 4, 5])
        self.assertEqual((c.reinicios, c.desordenados), (1, 0))

    def test_defecto_repetido_con_seq_pequeno_cuenta_como_reinicio(self):
        """DEFECTO menor, ya corregido (mismo método): la condición `seq <= 2` se evalúa antes que el caso
        "desordenado", así que un repetido o un desorden al principio de la secuencia (0,1,1 o
        0,2,1) se cuenta como reinicio. Caso exacto: [0, 1, 1] -> esperado desordenados=1,
        reinicios=0; obtenido reinicios=1. Solo afecta los primeros paquetes, impacto bajo."""
        c = self.contar([0, 1, 1])
        self.assertEqual((c.reinicios, c.desordenados), (0, 1))


# ---------------------------------------------------------------------------------------------
# 5. Jitter RFC 3550 con un caso calculado a mano
# ---------------------------------------------------------------------------------------------
class TestJitter(unittest.TestCase):

    def test_caso_a_mano(self):
        """El emisor manda cada 20 ms (t = 0, 20, 40, 60) y el receptor los ve llegar en su propio
        reloj en 100, 121, 139, 165 (los relojes NO están sincronizados: el 100 inicial no importa).

            par   llegadas   envíos   D = Δllegada - Δenvío   |D|   J = J + (|D| - J)/16
            1-2   121-100    20-0          21 - 20 =  1        1    0      + (1 - 0)/16           = 0.0625
            2-3   139-121    40-20         18 - 20 = -2        2    0.0625 + (2 - 0.0625)/16      = 0.18359375
            3-4   165-139    60-40         26 - 20 =  6        6    0.18359375 + (6 - 0.18359375)/16 = 0.547119140625

        Se eligieron números cuyas divisiones por 16 son exactas en binario, así que se compara con
        assertEqual sin tolerancia.
        """
        j = p.Jitter()
        self.assertEqual(j.registrar(0, 100), 0.0)        # el primero solo fija la referencia
        self.assertEqual(j.registrar(20, 121), 0.0625)
        self.assertEqual(j.registrar(40, 139), 0.18359375)
        self.assertEqual(j.registrar(60, 165), 0.547119140625)

    def test_retardo_constante_no_da_jitter(self):
        # Si la red tarda siempre lo mismo (aunque sean 300 ms), el jitter es 0: mide la VARIACIÓN.
        j = p.Jitter()
        for k in range(50):
            j.registrar(k * 20, 5000 + 300 + k * 20)
        self.assertEqual(j.j, 0.0)

    def test_converge_a_variacion_sostenida(self):
        # Llegadas que alternan +5/-5 ms alrededor de lo esperado: |D| = 10 en cada par (salvo el
        # primero), así que J tiende a 10 (el promedio móvil 1/16 se acerca exponencialmente).
        j = p.Jitter()
        for k in range(400):
            j.registrar(k * 20, k * 20 + (5 if k % 2 else -5))
        self.assertAlmostEqual(j.j, 10.0, places=6)

    def test_un_paquete_raro_mueve_poco(self):
        # Un solo paquete 160 ms tarde: |D| = 160 al llegar y otra vez 160 en el siguiente par
        # (porque el siguiente "adelanta"). J sube a 160/16 = 10 y luego 10 + 150/16 ≈ 19.4:
        # bastante menos que 160, que es justo la idea del filtro 1/16.
        j = p.Jitter()
        j.registrar(0, 0)
        j.registrar(20, 20)
        self.assertEqual(j.registrar(40, 200), 10.0)
        self.assertAlmostEqual(j.registrar(60, 60 + 0), 10.0 + (160 - 10.0) / 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
