# correr_todo.ps1 - Pruebas experimentales del despliegue Docker de NexoLab.
#
# Uso (PowerShell, desde cualquier carpeta):
#     powershell -ExecutionPolicy Bypass -File "pruebas\correr_todo.ps1"
#     powershell -ExecutionPolicy Bypass -File "pruebas\correr_todo.ps1" -MinutosBase 5 -SinLevantar
#
# Por qué este orden:
#   0. Pruebas unitarias del protocolo: no necesitan nada y, si fallan, no tiene sentido medir la red.
#   1. Levantar el stack con los ESP32 emulados y esperar a que el admin vea los 6 servicios en OK
#      (los robots PyBullet tardan unos segundos en cargar).
#   2. Aislamiento: es rápida y, si falla, las mediciones de latencia entre VLAN no significan lo que
#      dicen (el admin podría estar llegando a las zonas por otro camino).
#   3. Disponibilidad: apaga y prende contenedores; al terminar todo vuelve a quedar arriba.
#   4. Red base (5 min): la referencia, con todo sano y sin perturbar. Va DESPUÉS de la de
#      disponibilidad para que las caídas provocadas no ensucien la medición base, y con una pausa
#      para que el admin se asiente.
#   5. Red con retardo (tc netem en el router): se compara contra la base.
#   6. (opcional, -ConCarga) Red con carga: los emuladores a 100 Hz.
# Los resultados quedan en pruebas\resultados\ (json + png). Nada de esto sube nada a internet.

param(
    [double]$MinutosBase = 5,      # duración de la medición base
    [double]$MinutosOtros = 3,     # duración de retardo y carga
    [switch]$SinLevantar,          # el stack ya está arriba: no hacer "docker compose up"
    [switch]$ConCarga,             # correr también el escenario de carga
    [switch]$Bajar                 # al final, "docker compose down" del stack
)

$ErrorActionPreference = "Continue"
$Tema = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Tema "entorno\Scripts\python.exe"
$Pr = Join-Path $Tema "pruebas"
$Resumen = @()

function Paso($titulo) {
    Write-Host ""
    Write-Host "==== $titulo ====" -ForegroundColor Cyan
}

function Anotar($nombre, $codigo, [switch]$Medicion) {
    # Los scripts de Python devuelven 0 = aprobada, 1 = algún criterio falló, 2 = no se pudo correr.
    # medir_red.py no tiene criterio de aprobación (mide): 0 solo quiere decir que midió.
    $ok = if ($Medicion) { "MEDIDO (ver json)" } else { "APROBADA" }
    $estado = switch ($codigo) { 0 { $ok } 1 { "REVISAR" } default { "NO CORRIÓ ($codigo)" } }
    $script:Resumen += [pscustomobject]@{ Prueba = $nombre; Resultado = $estado }
}

if (-not (Test-Path $Py)) {
    Write-Host "No encuentro el entorno del tema ($Py). Créelo con: python -m venv entorno; entorno\Scripts\python -m pip install paho-mqtt matplotlib" -ForegroundColor Red
    exit 2
}

# ---- 0. Pruebas unitarias del protocolo (sin Docker) ----
Paso "0. Pruebas unitarias de comun/protocolo.py"
Push-Location $Tema
& $Py -m unittest pruebas.test_protocolo pruebas.test_nexo
Anotar "0 unitarias protocolo" $LASTEXITCODE
Pop-Location

# ---- 1. Levantar el stack con los emuladores ----
if (-not $SinLevantar) {
    Paso "1. docker compose --profile emulado up -d --build"
    Push-Location $Tema
    $env:NEXO_MODO = 'emulado'
    # --build: si alguien cambió un Dockerfile, se reconstruye; si no, usa la caché y es rápido.
    docker compose --profile emulado up -d --build
    Pop-Location
}

Paso "1b. Esperando a que el admin publique los 6 servicios en OK (máx. 3 min)"
# Se reutiliza el observador MQTT de las pruebas en vez de reimplementarlo en PowerShell.
& $Py -c @"
import sys; sys.path.insert(0, r'$Pr')
import lab_pruebas as L
if not L.hay_broker():
    print('no hay broker en localhost:1883'); sys.exit(2)
o = L.Observador(); o.iniciar()
t = o.esperar(lambda ob: all(ob.ultimo('lab/estado', s) == 'OK' for s in L.CON_LED), 180)
print({s: o.ultimo('lab/estado', s) for s in L.CON_LED}); o.parar()
sys.exit(0 if t else 1)
"@
if ($LASTEXITCODE -ne 0) {
    Write-Host "AVISO: no todos los servicios llegaron a OK; se sigue, pero revise 'docker compose logs'." -ForegroundColor Yellow
}

# ---- 2. Aislamiento ----
Paso "2. Aislamiento entre VLAN"
& $Py (Join-Path $Pr "prueba_aislamiento.py")
Anotar "2 aislamiento" $LASTEXITCODE

# ---- 3. Disponibilidad ----
Paso "3. Disponibilidad (caída de sim-pepper, player-2 y del admin)"
& $Py (Join-Path $Pr "prueba_disponibilidad.py")
Anotar "3 disponibilidad" $LASTEXITCODE

Write-Host "Pausa de 30 s para que el admin se asiente después de las caídas..."
Start-Sleep -Seconds 30

# ---- 4. Red base ----
Paso "4. Red, escenario base ($MinutosBase min)"
& $Py (Join-Path $Pr "medir_red.py") --escenario base --minutos $MinutosBase
Anotar "4 red base" $LASTEXITCODE -Medicion

# ---- 5. Red con retardo ----
Paso "5. Red, escenario retardo 30 ms +/- 10 ms en la VLAN 2 ($MinutosOtros min)"
& $Py (Join-Path $Pr "medir_red.py") --escenario retardo --minutos $MinutosOtros
Anotar "5 red retardo" $LASTEXITCODE -Medicion
# Por si se cortó a la mitad: borrar cualquier cola tc que haya quedado en el router (no hace daño).
& $Py (Join-Path $Pr "medir_red.py") --limpiar-retardo | Out-Null

# ---- 6. Red con carga (opcional) ----
if ($ConCarga) {
    Paso "6. Red, escenario carga: emuladores a 100 Hz ($MinutosOtros min)"
    & $Py (Join-Path $Pr "medir_red.py") --escenario carga --minutos $MinutosOtros --hz-carga 100
    Anotar "6 red carga" $LASTEXITCODE -Medicion
}

if ($Bajar) {
    Paso "Bajando el stack"
    Push-Location $Tema
    docker compose --profile emulado down
    Pop-Location
}

Paso "Resumen"
$Resumen | Format-Table -AutoSize
Write-Host "Resultados en $Pr\resultados"
