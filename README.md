# Trabajos del segundo corte · Microcontroladores

**Juan Felipe Romero González** · Ingeniería Mecatrónica

En este repositorio reúno las tareas 4, 5, 6 y 7 del segundo corte. Cada trabajo tiene su propio `README.md`, los programas del computador y de la ESP32, las conexiones y sus evidencias. Las tareas 4, 5 y 6 conservan sus carpetas `evidencia/` con imágenes y videos. La tarea 6 se divide en dos puntos. La tarea 7 contiene imágenes en `evidencias/`, registros de telemetría y explicación detallada; no tiene video.

| Tarea | Qué se desarrolló | Guía |
| --- | --- | --- |
| 4 · Control de luces por gestos | Una cámara reconoce gestos de hasta dos manos y envía órdenes por USB a una ESP32 que controla tres LED con PWM y dos secuencias. | [Abrir tarea 4](04-control-gestos-luces-esp32/README.md) |
| 5 · Brazo robótico | Cuatro potenciómetros conectados a una ESP32 controlan las articulaciones de un brazo URDF representado en PyBullet. | [Abrir tarea 5](05-brazo-robotico-esp32-pybullet/README.md) |
| 6 · Punto 1 | Un teclado matricial y una LCD conectados a la ESP32 permiten elegir cifras que el brazo virtual dibuja. | [Abrir punto 1](06-brazo-opencv/Punto_1/README.md) |
| 6 · Punto 2 | La cámara y una CNN reconocen una cifra; dos ESP32 la transmiten por USB y SPI hasta una pantalla OLED. | [Abrir punto 2](06-brazo-opencv/Punto_2/README.md) |
| 7 · Enjambre ACO | Tres ESP32 ejecutan colonia de hormigas y comparten aportes por WiFi; un gemelo PyBullet en Docker muestra las rutas y el avance lógico. | [Abrir tarea 7](07-enjambre-aco-esp32-pybullet/README.md) |

En las tareas 4, 5 y 6 el computador ejecuta la interfaz Python. Los archivos `main.py` indicados en cada guía se guardan en la ESP32 mediante Thonny; la excepción es la **ESP32-B del punto 2**, cuyo programa `.ino` se carga desde Arduino IDE porque actúa como esclava SPI. Cada carpeta conserva sus dependencias en `requirements.txt`, de modo que no hace falta subir un entorno virtual. El modelo de gestos y el modelo CNN entrenado sí están incluidos porque los programas los utilizan; no se incluyen las bases de datos descargadas para entrenar.

La tarea 7 utiliza MicroPython en las tres ESP32 y un panel web servido por Python dentro de Docker. Su README explica la arquitectura, ACO, estados, funciones de todos los módulos, instalación por terminal, registros CSV, resultados y las seis imágenes originales. El montaje reportó tres nodos en la meta con 40 pasos y **dos rutas distintas**; la prueba local sin placas se presenta por separado.

## Cómo están explicados los programas

Las guías de las tareas 4, 5 y 6 empiezan por la idea de la práctica y luego muestran **dos diagramas**. El primero enseña por dónde viajan los datos entre la entrada, el computador y la ESP32; el segundo desarrolla un detalle importante, como aceptar un gesto, validar una trama, separar los trazos de un número o recuperar la confirmación SPI. Después se explica cada archivo ejecutable con los nombres de sus funciones y un ejemplo que recorre el sistema completo.

| Guía detallada | Códigos que explica paso a paso |
| --- | --- |
| [Tarea 4: gestos y luces](04-control-gestos-luces-esp32/README.md) | `app.py`, `logic.py`, MicroPython de la ESP32 |
| [Tarea 5: brazo y sensores](05-brazo-robotico-esp32-pybullet/README.md) | Lectura ADC, `protocol.py`, `robot_sim.py`, `brazo.urdf`, `app_brazo.py` |
| [Tarea 6, punto 1: teclado y dibujo](06-brazo-opencv/Punto_1/README.md) | Teclado y LCD en ESP32, `teclado_serial.py`, interfaz, curvas, geometría y PyBullet |
| [Tarea 6, punto 2: cámara y OLED](06-brazo-opencv/Punto_2/README.md) | Interfaz, visión, CNN, entrenamiento, protocolo, placa maestra y placa esclava |
| [Tarea 7: enjambre ACO](07-enjambre-aco-esp32-pybullet/README.md) | Mapa, colonia, agente, protocolo, transporte, WiFi, puente UDP, CSV, PyBullet, HTTP, JavaScript y Docker |

Los diagramas están escritos en **Mermaid** dentro del Markdown y se muestran directamente en GitHub. Las tablas se reservaron para pines, órdenes y recorridos exactos; la explicación del desarrollo y de cada código está redactada en párrafos para poder seguirla durante la presentación.

## Cómo recorrer la entrega

Abre primero el README de la tarea que quieras presentar. Allí explico qué ocurre desde la entrada —un gesto, un potenciómetro, una tecla o un número ante la cámara— hasta la salida visible. Después aparecen los archivos que intervienen, las conexiones y los pasos para ejecutar esa tarea en Windows. Las guías de las tareas 4, 5 y 6 conservan **sus videos e imágenes**. En la tarea 7 aparece una galería comentada de seis imágenes y no hay video. Cada tarea es independiente: entra en la carpeta correspondiente antes de instalar dependencias o ejecutar una aplicación.

Los videos de las tareas anteriores conservan sus rutas originales:

- [Video de la tarea 4](04-control-gestos-luces-esp32/evidencia/demostracion.mp4)
- [Video de la tarea 5](05-brazo-robotico-esp32-pybullet/evidencia/demostracion.mp4)
- [Video de la tarea 6, punto 1](06-brazo-opencv/Punto_1/evidencia/demostracion.mp4)
- [Video de la tarea 6, punto 2](06-brazo-opencv/Punto_2/evidencia/demostracion.mp4)


La evidencia de la nueva tarea está en [la galería de la tarea 7](07-enjambre-aco-esp32-pybullet/README.md#14-evidencias-en-imágenes). También se conservan [el CSV del montaje V3](07-enjambre-aco-esp32-pybullet/data/telemetria_multirruta_v3.csv) y la documentación de pruebas locales, distinguiendo sus condiciones.

