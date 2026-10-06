// logica.h - La parte de la maestra que NO toca hardware: normalizar las lecturas, la zona muerta,
// el mapeo a grados, armar los mensajes del protocolo y leer las lineas que llegan (PONG por UDP y
// MANUAL por serie).
//
// Esta separado del .ino a proposito: es C++ puro (solo <stdio.h>, <stdlib.h>, <string.h> y
// <stdint.h>), sin nada de Arduino, asi que se puede compilar tambien en el PC y comprobar que cada
// linea sale EXACTAMENTE con el formato que acepta comun/protocolo.leer (lo hace
// firmware/prueba_formato/, sin ESP32). Si se cambia un formato aqui, hay que cambiarlo en
// protocolo.py, y al reves.
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------------------------------------
// Entradas
// ---------------------------------------------------------------------------------------------

static inline int limitarEntero(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
static inline float limitarFloat(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// Lleva una lectura cruda del ADC (0..4095, 12 bits) a -100..100 tomando `centro` como el 0.
// Se escala POR SEPARADO cada mitad: el centro de un joystick casi nunca cae en 2048 (con el ADC del
// ESP32, que no es lineal en los extremos, suele quedar entre 1750 y 1950), y si se usara una sola
// escala un lado llegaria a 100 y el otro se quedaria en 85. Asi los dos topes dan +-100.
static inline int normalizar(float crudo, float centro, int invertir) {
  float v;
  if (crudo >= centro) {
    float rango = 4095.0f - centro;
    v = rango > 1.0f ? (crudo - centro) * 100.0f / rango : 0.0f;
  } else {
    float rango = centro;
    v = rango > 1.0f ? (crudo - centro) * 100.0f / rango : 0.0f;
  }
  int e = (int)(v >= 0 ? v + 0.5f : v - 0.5f);   // redondeo al entero mas cercano
  e = limitarEntero(e, -100, 100);
  return invertir ? -e : e;
}

// Zona muerta: todo lo que este a menos de `zm` % del centro vale 0, y lo de afuera se re-escala para
// que siga llegando a +-100 (si no, despues de la zona muerta habria un salto de 0 a zm). Asi el
// mando arranca suave desde 0 en el borde de la zona.
static inline int aplicarZonaMuerta(int v, int zm) {
  if (zm <= 0) return v;
  if (zm >= 100) return 0;
  if (v > -zm && v < zm) return 0;
  int s = v > 0 ? 1 : -1;
  int a = v > 0 ? v : -v;
  int r = ((a - zm) * 100 + (100 - zm) / 2) / (100 - zm);   // con redondeo
  return s * limitarEntero(r, 0, 100);
}

// -100..100 -> [gmin, gmax] grados, lineal. Con -90..90, grados = 0,9 * v.
static inline float aGrados(int v, float gmin, float gmax) {
  v = limitarEntero(v, -100, 100);
  return gmin + (float)(v + 100) * (gmax - gmin) / 200.0f;
}

// ---------------------------------------------------------------------------------------------
// Mensajes que salen (ver comun/protocolo.py: armar_ctrl, armar_joints, armar_latido)
// ---------------------------------------------------------------------------------------------
// Todos devuelven lo que devuelve snprintf: la longitud de la linea (sin el '\0'), o un numero
// >= n si no cabia (entonces no se manda). Los enteros sin signo van con %lu y un cast a
// unsigned long: en el ESP32 uint32_t es "unsigned int" o "unsigned long" segun la version del
// compilador, y el cast evita la advertencia de formato en cualquiera de los dos.

// CTRL,<id 1-3>,<seq>,<t_ms>,<dir -100..100>,<vel -100..100>,<boton 0/1>
static inline int armarCtrl(char* buf, size_t n, int jugador, uint32_t seq, uint32_t t_ms,
                            int dir, int vel, int boton) {
  return snprintf(buf, n, "CTRL,%d,%lu,%lu,%d,%d,%d", jugador, (unsigned long)seq,
                  (unsigned long)t_ms, limitarEntero(dir, -100, 100), limitarEntero(vel, -100, 100),
                  boton ? 1 : 0);
}

// JOINTS,<robot>,<seq>,<t_ms>,<j1>,<j2>,<j3>,<boton 0/1>   (grados con un decimal, como el
// f"{j:.1f}" de protocolo.armar_joints: printf y Python redondean igual el mismo double)
static inline int armarJoints(char* buf, size_t n, const char* robot, uint32_t seq, uint32_t t_ms,
                              float j1, float j2, float j3, int boton) {
  return snprintf(buf, n, "JOINTS,%s,%lu,%lu,%.1f,%.1f,%.1f,%d", robot, (unsigned long)seq,
                  (unsigned long)t_ms, (double)j1, (double)j2, (double)j3, boton ? 1 : 0);
}

// HB,<origen>,<seq>,<t_ms>  y  PING,<origen>,<seq>,<t_ms>
static inline int armarLatido(char* buf, size_t n, const char* tipo, const char* origen,
                              uint32_t seq, uint32_t t_ms) {
  return snprintf(buf, n, "%s,%s,%lu,%lu", tipo, origen, (unsigned long)seq, (unsigned long)t_ms);
}

// ---------------------------------------------------------------------------------------------
// Lineas que entran
// ---------------------------------------------------------------------------------------------

// Lee un entero decimal de [p, fin) y deja `p` despues de el. Devuelve false si no hay un numero
// entero completo (por ejemplo "12a" o vacio). strtol sobre un trozo sin '\0' no sirve, por eso
// se copia el campo a un buffer chico. Es long long (64 bits) y no long a proposito: en el ESP32
// long es de 32 bits CON signo, y un t_ms de millis() pasa de 2^31 a los 24,8 dias de encendida;
// strtol lo recortaria a 2147483647 y el RTT saldria absurdo.
static inline bool campoEntero(const char*& p, char sep, long long& valor) {
  char tmp[16];
  size_t k = 0;
  while (*p && *p != sep) {
    if (k + 1 >= sizeof(tmp)) return false;
    tmp[k++] = *p++;
  }
  tmp[k] = '\0';
  if (sep != '\0' && *p == sep) p++;   // con sep '\0' (ultimo campo) NO se avanza: se saldria
                                       // de la cadena
  if (k == 0) return false;
  char* finNum = nullptr;
  valor = strtoll(tmp, &finNum, 10);
  return finNum && *finNum == '\0';
}

// PONG,<origen>,<seq>,<t_ms>  ->  true si es un PONG para `origen`, con su seq y su t_ms.
// El t_ms que vuelve es el que mando ESTA placa en su PING (el admin lo devuelve tal cual), asi
// que RTT = millis() - t_ms usa un solo reloj y no hace falta sincronizar nada.
static inline bool leerPong(const char* linea, const char* origen, uint32_t& seq, uint32_t& t_ms) {
  if (strncmp(linea, "PONG,", 5) != 0) return false;
  const char* p = linea + 5;
  size_t lo = strlen(origen);
  if (strncmp(p, origen, lo) != 0 || p[lo] != ',') return false;   // es de otra placa
  p += lo + 1;
  long long s, t;
  if (!campoEntero(p, ',', s)) return false;
  if (!campoEntero(p, '\0', t)) return false;
  if (s < 0 || t < 0 || s > 0xFFFFFFFFLL || t > 0xFFFFFFFFLL) return false;
  seq = (uint32_t)s;
  t_ms = (uint32_t)t;
  return true;
}

// MANUAL,<a>,<b>,<c>,<boton>  o  MANUAL,OFF   (llegan por el puerto serie, desde el preview.html)
//   devuelve 1 = valores nuevos en a, b, c, boton (limitados a -100..100 y 0/1)
//            0 = MANUAL,OFF
//           -1 = no es una linea MANUAL valida (se ignora)
static inline int leerManual(const char* linea, int& a, int& b, int& c, int& boton) {
  if (strncmp(linea, "MANUAL,", 7) != 0) return -1;
  const char* p = linea + 7;
  if (strcmp(p, "OFF") == 0) return 0;
  long long v[4];
  for (int i = 0; i < 4; i++) {
    if (!campoEntero(p, i < 3 ? ',' : '\0', v[i])) return -1;
  }
  if (*p != '\0') return -1;   // sobran campos
  // Se limita ANTES de pasar a int (un 99999999999 no debe dar la vuelta y quedar negativo).
  for (int i = 0; i < 3; i++) v[i] = v[i] < -100 ? -100 : (v[i] > 100 ? 100 : v[i]);
  a = (int)v[0];
  b = (int)v[1];
  c = (int)v[2];
  boton = v[3] ? 1 : 0;
  return 1;
}
