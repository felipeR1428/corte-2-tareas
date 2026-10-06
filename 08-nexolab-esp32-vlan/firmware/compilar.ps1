# compilar.ps1 - Compila el firmware del tema 11: la maestra con cada uno de sus seis roles y la
# esclava de LEDs, con arduino-cli (sin abrir el Arduino IDE).
#
# Uso (desde PowerShell, en cualquier carpeta):
#   .\compilar.ps1                                  compila los seis roles y la esclava
#   .\compilar.ps1 -Roles CTRL_SPOT                 solo la maestra del Spot
#   .\compilar.ps1 -Roles CTRL_2 -SinEsclava        solo la maestra de player-2
#   .\compilar.ps1 -SoloEsclava                     solo la esclava
#   .\compilar.ps1 -Demo                            maestras con MODO_DEMO=1 (entradas senoidales,
#                                                   para una placa sin nada cableado)
#   .\compilar.ps1 -Roles CTRL_1 -SinEsclava -Puerto COM5 -Subir    compila y sube a COM5
#
# Cada compilacion deja sus binarios en build\<nombre>\ dentro de esta carpeta (git la ignora: ver
# firmware\.gitignore). El rol se pasa al compilador como -DROL=CTRL_x; config.h solo define ROL si
# no vino de afuera, asi que el mismo sketch sirve para las seis placas sin editar nada.
# Al final imprime una tabla con el % de flash y de RAM de cada uno.
#
# Antes de subir a una placa de verdad: editar WIFI_SSID, WIFI_CLAVE e IP_PC_* en los dos config.h.
# Ojo con -Subir: sube a la placa conectada en -Puerto; para subir otro rol hay que conectar otra
# placa (cada ESP32 queda con UN rol).
param(
    [string[]]$Roles = @("CTRL_1", "CTRL_2", "CTRL_3", "CTRL_SPOT", "CTRL_PEPPER", "CTRL_NAO"),
    [switch]$SinEsclava,
    [switch]$SoloEsclava,
    [switch]$Demo,
    [string]$Puerto = "",
    [switch]$Subir
)

$cli = "C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
$placa = "esp32:esp32:esp32"   # "ESP32 Dev Module" (nucleo esp32 3.3.x)
$maestra = Join-Path $PSScriptRoot "esp32_maestra"
$esclava = Join-Path $PSScriptRoot "esp32_esclava_leds"
$salidaBase = Join-Path $PSScriptRoot "build"

if ($Subir -and -not $Puerto) { Write-Host "Falta -Puerto COMx para subir"; exit 1 }
if ($Subir -and (($Roles.Count -gt 1 -and -not $SoloEsclava) -or (-not $SinEsclava -and -not $SoloEsclava))) {
    # Subir varios firmwares seguidos al mismo puerto dejaria en la placa solo el ultimo.
    Write-Host "Para subir, elegir UN firmware: -Roles CTRL_x -SinEsclava, o -SoloEsclava"; exit 1
}

# La esclava usa PubSubClient (cliente MQTT); si no esta, se instala (pesa unos 45 KB).
$libs = & $cli lib list 2>$null | Out-String
if ($libs -notmatch "PubSubClient") {
    Write-Host "Instalando la libreria PubSubClient..."
    & $cli lib install PubSubClient
}

$resumen = @()

function Compilar([string]$nombre, [string]$sketch, [string]$flags) {
    $salida = Join-Path $salidaBase $nombre
    Write-Host "=== $nombre $flags ==="
    $argumentos = @("compile", "-b", $placa, "--warnings", "all", "--output-dir", $salida)
    if ($flags) { $argumentos += @("--build-property", "compiler.cpp.extra_flags=$flags") }
    $argumentos += $sketch
    $texto = & $cli @argumentos 2>&1 | Out-String
    Write-Host $texto
    if ($LASTEXITCODE -ne 0) { Write-Host "Fallo la compilacion de $nombre"; exit 1 }
    # arduino-cli imprime el uso en el idioma del sistema ("usa ... (68%)" / "uses ... (68%)"): se
    # toman los dos porcentajes en el orden en que salen (primero flash, despues RAM).
    $pct = [regex]::Matches($texto, "\((\d+)%\)") | ForEach-Object { $_.Groups[1].Value }
    $script:resumen += [pscustomobject]@{ Firmware = $nombre; Flash = "$($pct[0]) %"; RAM = "$($pct[1]) %" }
    if ($Subir) {
        & $cli upload -b $placa -p $Puerto --input-dir $salida $sketch
        if ($LASTEXITCODE -ne 0) { Write-Host "Fallo la subida de $nombre"; exit 1 }
    }
}

if (-not $SoloEsclava) {
    foreach ($rol in $Roles) {
        $flags = "-DROL=$rol"
        if ($Demo) { $flags += " -DMODO_DEMO=1" }
        Compilar ("maestra_" + $rol.ToLower()) $maestra $flags
    }
}
if ($SoloEsclava -or -not $SinEsclava) {
    Compilar "esclava_leds" $esclava ""
}

$resumen | Format-Table -AutoSize
