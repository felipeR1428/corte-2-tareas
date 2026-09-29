# Trabajos del segundo corte · Microcontroladores

**Juan Felipe Romero González** · Ingeniería Mecatrónica

En este repositorio reúno las tareas 4, 5 y 6 del segundo corte. Cada trabajo tiene su propio `README.md`, los programas del computador y de la ESP32, las conexiones necesarias y una carpeta `evidencia/` con capturas, fotografías y el video de la demostración. La tarea 6 se divide en dos puntos porque resuelven ejercicios distintos.

| Tarea | Qué se desarrolló | Guía |
| --- | --- | --- |
| 4 · Control de luces por gestos | Una cámara reconoce gestos de hasta dos manos y envía órdenes por USB a una ESP32 que controla tres LED con PWM y dos secuencias. | [Abrir tarea 4](04-control-gestos-luces-esp32/README.md) |
| 5 · Brazo robótico | Cuatro potenciómetros conectados a una ESP32 controlan las articulaciones de un brazo URDF representado en PyBullet. | [Abrir tarea 5](05-brazo-robotico-esp32-pybullet/README.md) |
| 6 · Punto 1 | Un teclado matricial y una LCD conectados a la ESP32 permiten elegir cifras que el brazo virtual dibuja. | [Abrir punto 1](06-brazo-opencv/Punto_1/README.md) |
| 6 · Punto 2 | La cámara y una CNN reconocen una cifra; dos ESP32 la transmiten por USB y SPI hasta una pantalla OLED. | [Abrir punto 2](06-brazo-opencv/Punto_2/README.md) |

En los cuatro ejercicios el computador ejecuta la interfaz Python. Los archivos `main.py` indicados en cada guía se guardan en la ESP32 mediante Thonny; la excepción es la **ESP32-B del punto 2**, cuyo programa `.ino` se carga desde Arduino IDE porque actúa como esclava SPI. Cada carpeta conserva sus dependencias en `requirements.txt`, de modo que no hace falta subir un entorno virtual. El modelo de gestos y el modelo CNN entrenado sí están incluidos porque los programas los utilizan; no se incluyen las bases de datos descargadas para entrenar.

## Cómo están explicados los programas

Cada README empieza por la idea de la práctica y luego muestra **dos diagramas**. El primero enseña por dónde viajan los datos entre la entrada, el computador y la ESP32; el segundo desarrolla un detalle importante, como aceptar un gesto, validar una trama, separar los trazos de un número o recuperar la confirmación SPI. Después se explica cada archivo ejecutable con los nombres de sus funciones y un ejemplo que recorre el sistema completo.

| Guía detallada | Códigos que explica paso a paso |
| --- | --- |
| [Tarea 4: gestos y luces](04-control-gestos-luces-esp32/README.md) | `app.py`, `logic.py`, MicroPython de la ESP32 |
| [Tarea 5: brazo y sensores](05-brazo-robotico-esp32-pybullet/README.md) | Lectura ADC, `protocol.py`, `robot_sim.py`, `brazo.urdf`, `app_brazo.py` |
| [Tarea 6, punto 1: teclado y dibujo](06-brazo-opencv/Punto_1/README.md) | Teclado y LCD en ESP32, `teclado_serial.py`, interfaz, curvas, geometría y PyBullet |
| [Tarea 6, punto 2: cámara y OLED](06-brazo-opencv/Punto_2/README.md) | Interfaz, visión, CNN, entrenamiento, protocolo, placa maestra y placa esclava |

Los diagramas están escritos en **Mermaid** dentro del Markdown y se muestran directamente en GitHub. Las tablas se reservaron para pines, órdenes y recorridos exactos; la explicación del desarrollo y de cada código está redactada en párrafos para poder seguirla durante la presentación.

## Cómo recorrer la entrega

Abre primero el README de la tarea que quieras presentar. Allí explico qué ocurre desde la entrada —un gesto, un potenciómetro, una tecla o un número ante la cámara— hasta la salida visible. Después aparecen los archivos que intervienen, las conexiones y los pasos para ejecutar esa tarea en Windows. Al final de cada guía están **su video y las imágenes del montaje y de la interfaz**. Las cuatro carpetas son independientes: entra en la carpeta correspondiente antes de instalar dependencias o ejecutar una aplicación.

La demostración de cada trabajo está en su propio archivo:

- [Video de la tarea 4](04-control-gestos-luces-esp32/evidencia/demostracion.mp4)
- [Video de la tarea 5](05-brazo-robotico-esp32-pybullet/evidencia/demostracion.mp4)
- [Video de la tarea 6, punto 1](06-brazo-opencv/Punto_1/evidencia/demostracion.mp4)
- [Video de la tarea 6, punto 2](06-brazo-opencv/Punto_2/evidencia/demostracion.mp4)

