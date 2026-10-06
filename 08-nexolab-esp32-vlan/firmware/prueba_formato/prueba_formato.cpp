// prueba_formato.cpp - Ejecuta en el PC las funciones de esp32_maestra/logica.h (las MISMAS que
// corren en el ESP32: es el mismo archivo) e imprime lo que producen, para que prueba_formato.py lo
// compare con comun/protocolo.py. No necesita ningun ESP32.
//
// Salida: una linea por caso, campos separados por '|':
//   C|jugador|seq|t_ms|dir|vel|boton|<linea CTRL>
//   J|robot|seq|t_ms|j1|j2|j3|boton|<linea JOINTS>     (j con 9 cifras: el float exacto)
//   L|tipo|origen|seq|t_ms|<linea>
//   N|crudo|centro|invertir|zm|<resultado normalizar + zona muerta>
//   G|v|gmin|gmax|<grados con 9 cifras>
//   M|<entrada>|<r>|a|b|c|boton
//   P|<entrada>|<ok>|seq|t_ms
#include "../esp32_maestra/logica.h"

int main() {
  char buf[128];

  // CTRL: valores dentro y fuera de rango, signos, seq/t_ms grandes (cerca del limite de 32 bits).
  const int dirs[] = {-150, -100, -37, -1, 0, 1, 42, 100, 150};
  const unsigned long seqs[] = {0UL, 1UL, 4294967295UL};
  for (int jug = 1; jug <= 3; jug++)
    for (int d : dirs)
      for (unsigned long s : seqs) {
        int vel = -d / 2, bot = (d & 1);
        armarCtrl(buf, sizeof(buf), jug, (uint32_t)s, (uint32_t)(s * 7u + 13u), d, vel, bot);
        printf("C|%d|%lu|%lu|%d|%d|%d|%s\n", jug, s, (unsigned long)(uint32_t)(s * 7u + 13u), d, vel,
               bot, buf);
      }

  // JOINTS: el camino real del firmware, entrada -100..100 -> aGrados -> armarJoints.
  const char* robots[] = {"spot", "pepper", "nao"};
  const float rangos[][2] = {{-90.0f, 90.0f}, {-45.0f, 30.0f}, {0.0f, 180.0f}};
  for (const char* r : robots)
    for (const auto& g : rangos)
      for (int v = -100; v <= 100; v += 1) {
        float j1 = aGrados(v, g[0], g[1]);
        float j2 = aGrados(-v, g[0], g[1]);
        float j3 = aGrados(v / 3, g[0], g[1]);
        int bot = v > 50;
        armarJoints(buf, sizeof(buf), r, (uint32_t)(v + 100), 123456u, j1, j2, j3, bot);
        printf("J|%s|%d|123456|%.9g|%.9g|%.9g|%d|%s\n", r, v + 100, (double)j1, (double)j2, (double)j3,
               bot, buf);
      }

  // HB / PING
  const char* origenes[] = {"ctrl-1", "ctrl-spot", "ctrl-pepper", "esclava"};
  for (const char* o : origenes) {
    armarLatido(buf, sizeof(buf), "HB", o, 17u, 98765u);
    printf("L|HB|%s|17|98765|%s\n", o, buf);
    armarLatido(buf, sizeof(buf), "PING", o, 4294967295u, 0u);
    printf("L|PING|%s|4294967295|0|%s\n", o, buf);
  }

  // Normalizar + zona muerta
  const float crudos[] = {0, 100, 1000, 1800, 1850, 1900, 1950, 2000, 2048, 3000, 4000, 4095};
  const float centros[] = {1850, 2048};
  for (float c : centros)
    for (float x : crudos)
      for (int inv = 0; inv <= 1; inv++)
        for (int zm = 0; zm <= 8; zm += 8) {
          printf("N|%.0f|%.0f|%d|%d|%d\n", x, c, inv, zm, aplicarZonaMuerta(normalizar(x, c, inv), zm));
        }

  // MANUAL
  const char* manuales[] = {"MANUAL,10,-20,30,1", "MANUAL,OFF", "MANUAL,500,-500,0,0", "MANUAL,1,2,3",
                            "MANUAL,1,2,3,4,5", "MANUAL,a,2,3,0", "MANUAL,,2,3,0", "MANUAL,1,2,3,",
                            "OTRA,1,2,3,0", "MANUAL,-100,100,0,7"};
  for (const char* m : manuales) {
    int a = 0, b = 0, c = 0, bt = 0;
    int r = leerManual(m, a, b, c, bt);
    printf("M|%s|%d|%d|%d|%d|%d\n", m, r, a, b, c, bt);
  }

  // PONG
  const char* pongs[] = {"PONG,ctrl-1,5,1234", "PONG,ctrl-10,5,1234", "PONG,ctrl-2,5,1234",
                         "PONG,ctrl-1,5", "PONG,ctrl-1,5,1234,9", "PING,ctrl-1,5,1234",
                         "PONG,ctrl-1,x,1234", "PONG,ctrl-1,4294967295,4294967295"};
  for (const char* p : pongs) {
    uint32_t s = 0, t = 0;
    bool ok = leerPong(p, "ctrl-1", s, t);
    printf("P|%s|%d|%lu|%lu\n", p, ok ? 1 : 0, (unsigned long)s, (unsigned long)t);
  }
  return 0;
}
