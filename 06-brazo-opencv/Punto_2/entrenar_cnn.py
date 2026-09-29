"""Entrena CNN con MNIST y EMNIST Digits y valida ambos por separado."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import ConcatDataset, DataLoader
from torchvision import datasets, transforms

from cnn_model import DigitCNN
from orientacion_emnist import upright


BASE = Path(__file__).resolve().parent
MODEL_PATH = BASE / "modelos" / "digitos_cnn.pt"


def accuracy(model, loader, device):
    correct = total = 0
    model.eval()
    with torch.inference_mode():
        for images, labels in loader:
            predictions = model(images.to(device)).argmax(dim=1)
            correct += int((predictions == labels.to(device)).sum().item())
            total += len(images)
    return correct / total


def main():
    parser = argparse.ArgumentParser(description="Entrenar CNN con MNIST + EMNIST Digits")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.epochs <= 40 or not 8 <= args.batch_size <= 1024:
        parser.error("Epocas 1-40 y lote 8-1024")

    torch.manual_seed(42)
    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Dispositivo de entrenamiento: {device}", flush=True)

    folder = BASE / "datos_mnist"
    augmented = transforms.Compose([
        transforms.RandomAffine(degrees=12, translate=(0.10, 0.10),
                                scale=(0.88, 1.12), shear=5),
        transforms.ToTensor(),
    ])
    emnist_augmented = transforms.Compose([
        transforms.Lambda(upright),
        transforms.RandomAffine(degrees=12, translate=(0.10, 0.10),
                                scale=(0.88, 1.12), shear=5),
        transforms.ToTensor(),
    ])
    print("Preparando MNIST y EMNIST Digits; la descarga inicial puede tardar.", flush=True)
    mnist_train = datasets.MNIST(root=folder, train=True, download=True,
                                 transform=augmented)
    emnist_train = datasets.EMNIST(root=folder, split="digits", train=True,
                                   download=True, transform=emnist_augmented)
    mnist_test = datasets.MNIST(root=folder, train=False, download=True,
                                transform=transforms.ToTensor())
    emnist_test = datasets.EMNIST(root=folder, split="digits", train=False,
                                  download=True, transform=transforms.Compose([
                                      transforms.Lambda(upright), transforms.ToTensor(),
                                  ]))
    training_set = ConcatDataset([mnist_train, emnist_train])
    training = DataLoader(training_set, batch_size=args.batch_size, shuffle=True,
                          num_workers=0, pin_memory=device.type == "cuda")
    tests = {
        "MNIST": DataLoader(mnist_test, batch_size=512, num_workers=0),
        "EMNIST Digits": DataLoader(emnist_test, batch_size=512, num_workers=0),
    }
    net = DigitCNN().to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=0.001)
    loss_fn = nn.CrossEntropyLoss()
    print(f"Entrenamiento: {len(mnist_train)} MNIST + {len(emnist_train)} "
          f"EMNIST Digits = {len(training_set)} imagenes.", flush=True)

    best_score = -1.0
    best_weights = None
    best_result = {}
    best_epoch = 0
    for epoch in range(1, args.epochs + 1):
        net.train()
        train_loss = 0.0
        for batch_number, (images, labels) in enumerate(training, 1):
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(net(images), labels)
            loss.backward()
            optimizer.step()
            train_loss += float(loss.item()) * len(images)
            if batch_number % max(1, len(training) // 6) == 0:
                print(f"Epoca {epoch}/{args.epochs}: lote "
                      f"{batch_number}/{len(training)}", flush=True)
        result = {name: accuracy(net, loader, device) for name, loader in tests.items()}
        score = (result["MNIST"] + result["EMNIST Digits"]) / 2
        if score > best_score:
            best_score = score
            best_result = result
            best_epoch = epoch
            best_weights = {key: value.detach().cpu().clone()
                            for key, value in net.state_dict().items()}
        print(f"Epoca {epoch}/{args.epochs} | perdida "
              f"{train_loss / len(training_set):.4f} | "
              f"MNIST {result['MNIST']:.2%} | "
              f"EMNIST Digits {result['EMNIST Digits']:.2%}", flush=True)

    MODEL_PATH.parent.mkdir(exist_ok=True)
    torch.save({"format": "digit_cnn_v1", "weights": best_weights,
                "sources": ("MNIST", "EMNIST Digits"),
                "train_size": len(training_set), "best_epoch": best_epoch,
                "mnist_accuracy": best_result["MNIST"],
                "emnist_accuracy": best_result["EMNIST Digits"]}, MODEL_PATH)
    print(f"Guardado: {MODEL_PATH} (mejor epoca {best_epoch})", flush=True)
    print("Los aciertos en MNIST/EMNIST no garantizan cero errores con tu camara.", flush=True)


if __name__ == "__main__":
    main()
