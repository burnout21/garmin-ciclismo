# Registra una tarea diaria en el Programador de tareas de Windows que ejecuta la importación.
# Si el PC está apagado a la hora indicada, se ejecuta en cuanto se encienda (StartWhenAvailable).
param(
    [string]$Hora = "08:00"
)

$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command py -ErrorAction Stop).Source

$accion = New-ScheduledTaskAction -Execute $py -Argument "`"$dir\garmin_ciclismo.py`" --publicar" -WorkingDirectory $dir
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask -TaskName "Garmin Ciclismo" -Action $accion -Trigger $disparador -Settings $opciones `
    -Description "Importa las actividades de ciclismo del día anterior desde Garmin Connect y las sube a GitHub" -Force

Write-Host "Tarea 'Garmin Ciclismo' registrada a las $Hora."
