# Ejecutar desde PowerShell: .\iniciar.ps1 -Modo emulado
param([ValidateSet('emulado','fisico')][string]$Modo='emulado',[switch]$Reconstruir)
$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
$env:NEXO_MODO=$Modo
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Abra Docker Desktop y espere a que inicie.' }
$opciones=@('compose','--profile','emulado')
if ($Modo -eq 'fisico') {
    # Evita que queden mandos emulados activos cuando se conectan las placas físicas.
    & docker @opciones stop ctrl-1 ctrl-2 ctrl-3 ctrl-spot ctrl-pepper ctrl-nao esclava-emulada
    if ($LASTEXITCODE -ne 0) { throw 'No se pudieron detener los mandos emulados.' }
    $opciones=@('compose')
}
$opciones+=@('up','-d')
if ($Reconstruir) { $opciones+='--build' }
& docker @opciones
if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar el laboratorio.' }
Start-Process 'http://127.0.0.1:18180'
