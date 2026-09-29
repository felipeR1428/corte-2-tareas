# Tarea 6 · Brazo y visión por computador

Esta tarea contiene dos puntos separados. En el [punto 1](Punto_1/README.md) la entrada es una tecla del teclado 4×4: la ESP32 la identifica y la aplicación mueve un brazo de PyBullet para dibujar el número. En el [punto 2](Punto_2/README.md) la entrada es una imagen de cámara: OpenCV aísla el dígito, la CNN lo clasifica y dos ESP32 se comunican por SPI para mostrarlo en una OLED.

Aunque ambas partes hablan de números, cada una tiene su propio programa de PC, `requirements.txt`, firmware y carpeta `evidencia/`. Para trabajar en ellas, abre `Punto_1/` o `Punto_2/` como carpeta de trabajo y sigue las conexiones del README respectivo. Si reutilizas la misma ESP32 como placa A entre los puntos, vuelve a guardar el `main.py` que corresponde al punto elegido.

| Parte | Entrada y recorrido | Evidencia |
| --- | --- | --- |
| [Punto 1](Punto_1/README.md) | Teclado → ESP32 → USB → PyBullet → trazo del brazo | [Fotos y video](Punto_1/evidencia/) |
| [Punto 2](Punto_2/README.md) | Cámara → CNN → ESP32-A → SPI → ESP32-B → OLED | [Fotos y video](Punto_2/evidencia/) |

## Diferencia entre los dos recorridos

En el punto 1 la cifra ya se conoce desde el primer momento porque el usuario la selecciona en el teclado. La parte más importante del código consiste en reconocer la pulsación, convertirla en una trama confiable y calcular los trazos y las articulaciones del brazo virtual. El [diagrama de lectura y dibujo](Punto_1/README.md) y la [explicación de cada archivo](Punto_1/README.md) muestran cómo se logra.

En el punto 2 la cifra no se conoce de antemano: primero hay que obtenerla de la imagen de la cámara. Después la aplicación debe comprobar que la CNN la reconoce de forma estable y enviarla por dos enlaces distintos hasta la OLED. Su [diagrama de reconocimiento y comunicación](Punto_2/README.md) separa esas etapas; la [explicación de todos los códigos](Punto_2/README.md) incluye tanto el entrenamiento como los programas de las dos placas.

Las imágenes de `referencia/` se conservan como material del ejercicio y las fotografías, capturas y videos propios están en `evidencia/` de **cada punto**. Las capturas generadas con los botones de las aplicaciones se guardan localmente en `capturas/`, mientras que las evidencias elegidas para el repositorio ya están organizadas en sus carpetas.
