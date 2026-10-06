# Publica las seis imágenes de NexoLab en la cuenta indicada explícitamente.
# Antes: docker login
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9][a-z0-9_-]+$')][string]$Usuario,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')][string]$Version = '3.0'
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:DOCKERHUB_USUARIO = $Usuario
$env:NEXO_VERSION = $Version
$env:ACEPTO_LICENCIA_SOFTBANK = 'no'
docker info *> $null
if ($LASTEXITCODE -ne 0) { throw 'Abra Docker Desktop antes de publicar.' }
docker compose --profile emulado build
if ($LASTEXITCODE -ne 0) { throw 'Falló la construcción. No se publica.' }
docker compose --profile emulado push
if ($LASTEXITCODE -ne 0) { throw 'La publicación no terminó. Revise el inicio de sesión y los permisos.' }
$imagenes = @('router','admin','servidor-pista','jugador','robot','emulador')
$enlaces = @($imagenes | ForEach-Object { "https://hub.docker.com/r/$Usuario/nexolab-$_" })
New-Item -ItemType Directory -Force resultados | Out-Null
@{ fecha=(Get-Date).ToUniversalTime().ToString('o'); usuario=$Usuario; version=$Version; enlaces=$enlaces } |
    ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 resultados/publicacion_dockerhub.json
Write-Host 'Publicación completada:'
$enlaces | ForEach-Object { Write-Host $_ }
