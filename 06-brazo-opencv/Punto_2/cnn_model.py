"""CNN de clasificacion 28x28; arquitectura compartida por entrenamiento y app."""

from __future__ import annotations

import torch
from torch import nn


class DigitCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Conv2d(1, 32, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),
            nn.Flatten(),
            nn.Linear(64 * 7 * 7, 128),
            nn.ReLU(),
            nn.Dropout(0.35),
            nn.Linear(128, 10),
        )

    def forward(self, x):
        return self.layers(x)


def load_model(path):
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint.get("format") != "digit_cnn_v1":
        raise ValueError("Modelo de CNN incompatible")
    model = DigitCNN()
    model.load_state_dict(checkpoint["weights"])
    model.eval()
    model.training_sources = tuple(checkpoint.get("sources", ("MNIST",)))
    return model
