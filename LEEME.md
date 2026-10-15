# Garmin Ciclismo

Importa las actividades de ciclismo de Garmin Connect, las deja en `datos/` y las sube a GitHub para que ChatGPT pueda leerlas.

- `datos/actividades_ciclismo.md`: resumen mensual + tabla de actividades (el más cómodo para ChatGPT).
- `datos/actividades_ciclismo.csv`: todas las columnas (FC, potencia, TSS, efecto de entrenamiento...).
- `datos/actividades_ciclismo.json`: lo mismo en JSON.

## Puesta en marcha

```
py -m pip install -r requirements.txt
py garmin_ciclismo.py --login
py garmin_ciclismo.py --desde 2024-01-01
```

1. `--login` pide email, contraseña y código MFA si lo tienes. Solo guarda los tokens de sesión en `%USERPROFILE%\.garminconnect`, fuera del repo (no la contraseña). Duran aproximadamente un año; cuando caduquen, el log lo indicará y habrá que repetir `--login`.
2. `--desde` hace la carga histórica (una vez).

## Subir a GitHub

1. Crea el repositorio vacío `garmin-ciclismo` en https://github.com/new (sin README).
2. Primer push:

```
git remote add origin https://github.com/burnout21/garmin-ciclismo.git
git push -u origin main
```

3. Instala la tarea diaria (08:00; si el PC está apagado, se ejecuta al encenderlo). Cada día importa lo de ayer y hace commit + push de `datos/` si hay algo nuevo:

```
powershell -ExecutionPolicy Bypass -File instalar_tarea.ps1
```

El registro de ejecuciones queda en `garmin_ciclismo.log`.

## Que ChatGPT lo lea

- **Repositorio público**: dale a ChatGPT la URL del fichero, mejor en las instrucciones de un Proyecto para no repetirla:
  `https://raw.githubusercontent.com/burnout21/garmin-ciclismo/main/datos/actividades_ciclismo.md`
  (o `.../actividades_ciclismo.csv` si quieres que haga cálculos con todas las columnas).
- **Repositorio privado**: conecta GitHub en ChatGPT (Configuración → Aplicaciones/Conectores → GitHub), da acceso a este repo y pídele que lea `datos/actividades_ciclismo.md`.

Ojo: en un repo público cualquiera puede ver tus actividades (fechas, lugar, FC, potencia).

Nota: usa la librería no oficial `garminconnect` (Garmin no ofrece API pública para particulares); si Garmin cambia su login puede requerir actualizarla con `py -m pip install -U garminconnect`.
