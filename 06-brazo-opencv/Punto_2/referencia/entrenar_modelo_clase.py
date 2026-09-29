# entrenar_modelo.py
# Entrena una CNN sobre MNIST y guarda el modelo en disco.
# Ejecutar UNA SOLA VEZ antes de usar el reconocedor.

import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

import tensorflow as tf

# 1. Cargar MNIST
mnist = tf.keras.datasets.mnist
(x_train, y_train), (x_test, y_test) = mnist.load_data()

# 2. Normalizar
x_train = x_train / 255.0
x_test  = x_test  / 255.0

# 3. Reshape para CNN (canal de entrada = 1)
x_train = x_train.reshape(-1, 28, 28, 1)
x_test  = x_test.reshape(-1, 28, 28, 1)

# 4. Construir CNN (mucho más robusta que la red densa)
modelo = tf.keras.Sequential([
    tf.keras.layers.Conv2D(32, (3,3), activation='relu',
                           input_shape=(28,28,1)),
    tf.keras.layers.MaxPooling2D((2,2)),
    tf.keras.layers.Conv2D(64, (3,3), activation='relu'),
    tf.keras.layers.MaxPooling2D((2,2)),
    tf.keras.layers.Flatten(),
    tf.keras.layers.Dense(128, activation='relu'),
    tf.keras.layers.Dropout(0.5),
    tf.keras.layers.Dense(10, activation='softmax')
])

modelo.compile(optimizer='adam',
               loss='sparse_categorical_crossentropy',
               metrics=['accuracy'])

# 5. Entrenar con data augmentation (rotaciones y traslaciones leves)
datagen = tf.keras.preprocessing.image.ImageDataGenerator(
    rotation_range=10,
    width_shift_range=0.1,
    height_shift_range=0.1,
    zoom_range=0.1
)

modelo.fit(datagen.flow(x_train, y_train, batch_size=32),
           epochs=10,
           validation_data=(x_test, y_test))

# 6. Guardar
modelo.save('modelo_mnist_cnn.h5')
print("Modelo guardado como modelo_mnist_cnn.h5")