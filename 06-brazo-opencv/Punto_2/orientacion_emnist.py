"""Corrige la orientacion de EMNIST tal como se entrega en torchvision."""

from PIL import Image


def upright(image: Image.Image) -> Image.Image:
    """Transposicion de ejes (giro 90° + reflejo), sin cambiar el numero."""
    return image.transpose(Image.Transpose.TRANSPOSE)
