"""
Importador de actividades de ciclismo de Garmin Connect.

Descarga las actividades de ciclismo y las guarda en formatos que ChatGPT
puede leer directamente (CSV + Markdown + JSON).

Uso:
    py garmin_ciclismo.py --login                 Primer inicio de sesión (interactivo, admite MFA)
    py garmin_ciclismo.py                         Revisión diaria: desde la última revisión hasta ayer
    py garmin_ciclismo.py --desde 2024-01-01      Importación histórica desde una fecha
    py garmin_ciclismo.py --desde 2025-01-01 --hasta 2025-03-31
    py garmin_ciclismo.py --publicar              Revisión diaria + commit y push de datos/ a GitHub
"""

import argparse
import csv
import getpass
import json
import logging
import os
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from garminconnect import Garmin

BASE_DIR = Path(__file__).resolve().parent
TOKEN_DIR = Path(os.environ.get("GARMIN_TOKENS", Path.home() / ".garminconnect"))
SALIDA_DIR = Path(os.environ.get("GARMIN_SALIDA", BASE_DIR / "datos"))
ESTADO_FILE = BASE_DIR / "estado.json"
LOG_FILE = BASE_DIR / "garmin_ciclismo.log"

CSV_FILE = SALIDA_DIR / "actividades_ciclismo.csv"
MD_FILE = SALIDA_DIR / "actividades_ciclismo.md"
JSON_FILE = SALIDA_DIR / "actividades_ciclismo.json"

# Tipos de actividad de Garmin considerados ciclismo
PALABRAS_CICLISMO = ("cycling", "biking", "virtual_ride", "bmx", "cyclocross", "e_bike")

NOMBRES_TIPO = {
    "mountain_biking": "MTB", "road_biking": "Carretera", "cycling": "Ciclismo",
    "indoor_cycling": "Rodillo", "virtual_ride": "Rodillo virtual", "gravel_cycling": "Gravel",
}

# Mejores potencias medias de Garmin: segundos -> etiqueta de la columna
MEJORES_POTENCIAS = {1: "1s", 2: "2s", 5: "5s", 10: "10s", 20: "20s", 30: "30s", 60: "1min",
                     120: "2min", 300: "5min", 600: "10min", 1200: "20min", 1800: "30min", 3600: "60min"}

# (columna, campo de Garmin, conversión, descripción)
# Conversión: decimales, (decimales, factor), "hms" (segundos a h:mm:ss), "min" (segundos a minutos) o "texto".
# No se incluyen coordenadas GPS ni datos del perfil porque el repositorio es público.
CAMPOS = [
    ("distancia_km", "distance", (2, 0.001), "Distancia (km)"),
    ("duracion", "duration", "hms", "Tiempo registrado"),
    ("tiempo_movimiento", "movingDuration", "hms", "Tiempo en movimiento"),
    ("tiempo_transcurrido", "elapsedDuration", "hms", "Tiempo total incluidas las pausas"),
    ("velocidad_media_kmh", "averageSpeed", (1, 3.6), "Velocidad media (km/h)"),
    ("velocidad_max_kmh", "maxSpeed", (1, 3.6), "Velocidad máxima (km/h)"),
    ("mejor_tiempo_40km", "fastestSplit_40000", "hms", "Mejor tiempo en 40 km dentro de la salida"),
    ("desnivel_positivo_m", "elevationGain", 0, "Desnivel acumulado de subida (m)"),
    ("desnivel_negativo_m", "elevationLoss", 0, "Desnivel acumulado de bajada (m)"),
    ("altitud_min_m", "minElevation", 0, "Altitud mínima (m)"),
    ("altitud_max_m", "maxElevation", 0, "Altitud máxima (m)"),
    ("altitud_media_m", "avgElevation", 0, "Altitud media (m)"),
    ("velocidad_vertical_max_ms", "maxVerticalSpeed", 1, "Velocidad vertical máxima (m/s)"),
    ("vueltas", "lapCount", 0, "Número de vueltas (laps)"),

    ("fc_media", "averageHR", 0, "Frecuencia cardíaca media (ppm)"),
    ("fc_max", "maxHR", 0, "Frecuencia cardíaca máxima (ppm)"),
    ("fc_umbral_lactato", "lactateThresholdBpm", 0, "FC de umbral de lactato detectada (ppm)"),
    *[(f"min_zona_fc_{z}", f"hrTimeInZone_{z}", "min", f"Minutos en zona {z} de pulso") for z in range(1, 6)],

    ("potencia_media_w", "avgPower", 0, "Potencia media (W)"),
    ("potencia_normalizada_w", "normPower", 0, "Potencia normalizada (W)"),
    ("potencia_max_w", "maxPower", 0, "Potencia máxima (W)"),
    ("ftp_w", "maxFtp", 0, "FTP estimado por Garmin en esa fecha (W)"),
    ("potencia_media_sentado_w", "avgSeatedPower", 0, "Potencia media sentado (W)"),
    ("potencia_max_sentado_w", "maxSeatedPower", 0, "Potencia máxima sentado (W)"),
    ("potencia_media_de_pie_w", "avgStandingPower", 0, "Potencia media de pie (W)"),
    ("potencia_max_de_pie_w", "maxStandingPower", 0, "Potencia máxima de pie (W)"),
    ("tiempo_sentado", "seatedTime", "hms", "Tiempo pedaleando sentado"),
    ("tiempo_de_pie", "standingTime", "hms", "Tiempo pedaleando de pie"),
    ("balance_izquierda_pct", "avgLeftBalance", 0, "Porcentaje de potencia de la pierna izquierda"),
    *[(f"mejor_potencia_{e}_w", f"maxAvgPower_{s}", 0, f"Mejor potencia media en {e} (W)")
      for s, e in MEJORES_POTENCIAS.items()],
    *[(f"min_zona_potencia_{z}", f"powerTimeInZone_{z}", "min", f"Minutos en zona {z} de potencia")
      for z in range(1, 8)],
    ("if", "intensityFactor", 2, "Factor de intensidad (NP / FTP)"),
    ("tss", "trainingStressScore", 0, "Training Stress Score"),

    ("cadencia_media_rpm", "averageBikingCadenceInRevPerMinute", 0, "Cadencia media (rpm)"),
    ("cadencia_max_rpm", "maxBikingCadenceInRevPerMinute", 0, "Cadencia máxima (rpm)"),
    ("pedaladas", "strokes", 0, "Pedaladas totales"),

    ("efecto_aerobico", "aerobicTrainingEffect", 1, "Efecto de entrenamiento aeróbico (0-5)"),
    ("efecto_anaerobico", "anaerobicTrainingEffect", 1, "Efecto de entrenamiento anaeróbico (0-5)"),
    ("tipo_entrenamiento", "trainingEffectLabel", "texto", "Beneficio principal según Garmin"),
    ("mensaje_aerobico", "aerobicTrainingEffectMessage", "texto", "Valoración aeróbica de Garmin"),
    ("mensaje_anaerobico", "anaerobicTrainingEffectMessage", "texto", "Valoración anaeróbica de Garmin"),
    ("carga_entrenamiento", "activityTrainingLoad", 0, "Carga de entrenamiento de Garmin"),
    ("vo2max", "vO2MaxValue", 0, "VO2max estimado"),
    ("min_intensidad_moderada", "moderateIntensityMinutes", 0, "Minutos de intensidad moderada"),
    ("min_intensidad_vigorosa", "vigorousIntensityMinutes", 0, "Minutos de intensidad vigorosa"),

    ("respiracion_media_rpm", "avgRespirationRate", 0, "Frecuencia respiratoria media (resp/min)"),
    ("respiracion_min_rpm", "minRespirationRate", 0, "Frecuencia respiratoria mínima (resp/min)"),
    ("respiracion_max_rpm", "maxRespirationRate", 0, "Frecuencia respiratoria máxima (resp/min)"),

    ("grit", "grit", 1, "MTB: dificultad técnica del recorrido (Grit)"),
    ("flow", "avgFlow", 1, "MTB: fluidez en el descenso (Flow, menor es mejor)"),
    ("saltos", "jumpCount", 0, "MTB: número de saltos"),

    ("calorias", "calories", 0, "Calorías gastadas"),
    ("calorias_basales", "bmrCalories", 0, "Calorías del metabolismo basal durante la salida"),
    ("calorias_consumidas", "caloriesConsumed", 0, "Calorías ingeridas registradas"),
    ("agua_estimada_ml", "waterEstimated", 0, "Pérdida de líquido estimada (ml)"),
    ("agua_consumida_ml", "waterConsumed", 0, "Líquido ingerido registrado (ml)"),
    ("temperatura_min_c", "minTemperature", 0, "Temperatura mínima (ºC)"),
    ("temperatura_max_c", "maxTemperature", 0, "Temperatura máxima (ºC)"),
    ("descripcion", "description", "texto", "Notas escritas en Garmin"),
]

COLUMNAS_BASE = ["id", "fecha", "hora_inicio", "nombre", "tipo", "lugar"]
COLUMNAS = COLUMNAS_BASE + [c[0] for c in CAMPOS]

log = logging.getLogger("garmin_ciclismo")


def configurar_log():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def conectar(interactivo=False):
    """Usa los tokens guardados; si no hay o caducaron, pide credenciales (solo en modo interactivo)."""
    try:
        api = Garmin()
        api.login(str(TOKEN_DIR))
        return api
    except Exception as e:
        if not interactivo:
            log.error("No hay sesión válida (%s). Ejecuta: py garmin_ciclismo.py --login", e)
            sys.exit(1)

    email = os.environ.get("GARMIN_EMAIL") or input("Email de Garmin Connect: ")
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Contraseña: ")
    api = Garmin(email=email, password=password, prompt_mfa=lambda: input("Código MFA: "))
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    api.login(str(TOKEN_DIR))
    log.info("Sesión guardada en %s (la contraseña no se almacena)", TOKEN_DIR)
    return api


def es_ciclismo(act):
    tipo = (act.get("activityType") or {}).get("typeKey", "")
    return any(p in tipo for p in PALABRAS_CICLISMO)


def seg_a_hms(seg):
    if not seg:
        return ""
    seg = int(round(seg))
    return f"{seg // 3600}:{seg % 3600 // 60:02d}:{seg % 60:02d}"


def redondear(valor, decimales=1, factor=1.0):
    if valor is None:
        return ""
    valor = round(valor * factor, decimales)
    return int(valor) if decimales == 0 else valor


def convertir(valor, conversion):
    if valor is None or valor == "":
        return ""

    if conversion == "texto":
        return str(valor).replace("\n", " ").strip()

    if conversion == "hms":
        return seg_a_hms(valor)

    if conversion == "min":
        return redondear(valor, 1, 1 / 60)

    if isinstance(conversion, tuple):
        return redondear(valor, *conversion)

    return redondear(valor, conversion)


def resumir(act):
    inicio = act.get("startTimeLocal", "")
    tipo = (act.get("activityType") or {}).get("typeKey", "")
    lugar = act.get("locationName") or ""

    # Garmin no devuelve nombre en muchas salidas recientes
    nombre = act.get("activityName") or f"{NOMBRES_TIPO.get(tipo, tipo)} {lugar}".strip()

    resumen = {
        "id": act.get("activityId"),
        "fecha": inicio[:10],
        "hora_inicio": inicio[11:16],
        "nombre": nombre,
        "tipo": tipo,
        "lugar": lugar,
    }
    for columna, campo, conversion, _ in CAMPOS:
        resumen[columna] = convertir(act.get(campo), conversion)

    return resumen


def cargar_existentes():
    if not JSON_FILE.exists():
        return {}
    with open(JSON_FILE, encoding="utf-8") as f:
        return {a["id"]: a for a in json.load(f)}


def cargar_estado():
    if ESTADO_FILE.exists():
        with open(ESTADO_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_estado(estado):
    with open(ESTADO_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2)


def num(v):
    return v if isinstance(v, (int, float)) else 0


def escribir_salidas(actividades):
    SALIDA_DIR.mkdir(parents=True, exist_ok=True)
    lista = sorted(actividades.values(), key=lambda a: (a["fecha"], a["hora_inicio"]), reverse=True)

    # Una actividad por línea: fichero más ligero y diffs legibles en GitHub
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(a, ensure_ascii=False) for a in lista) + "\n]\n")

    # utf-8-sig para que Excel también lo abra bien
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(lista)

    escribir_markdown(lista)


def escribir_markdown(lista):
    meses = defaultdict(lambda: {"n": 0, "km": 0.0, "desnivel": 0.0, "seg": 0, "fc": [], "carga": 0.0})
    for a in lista:
        m = meses[a["fecha"][:7]]
        m["n"] += 1
        m["km"] += num(a["distancia_km"])
        m["desnivel"] += num(a["desnivel_positivo_m"])
        m["carga"] += num(a["carga_entrenamiento"])
        if a["fc_media"] != "":
            m["fc"].append(a["fc_media"])
        h, mi, s = (a["tiempo_movimiento"] or a["duracion"] or "0:00:00").split(":")
        m["seg"] += int(h) * 3600 + int(mi) * 60 + int(s)

    lineas = [
        "# Actividades de ciclismo (Garmin Connect)",
        "",
        f"Última actividad: {lista[0]['fecha'] if lista else '-'}. Total de actividades: {len(lista)}.",
        "",
        "Unidades: distancia en km, velocidad en km/h, desnivel y altitud en m, FC en ppm, "
        "potencia en W, cadencia en rpm. Duraciones en h:mm:ss. Campos vacíos = dato no registrado.",
        "",
        "## Resumen mensual",
        "",
        "| Mes | Salidas | Km | Desnivel + (m) | Tiempo en movimiento | FC media | Carga total |",
        "|---|---|---|---|---|---|---|",
    ]
    for mes in sorted(meses, reverse=True):
        m = meses[mes]
        fc = f"{sum(m['fc']) / len(m['fc']):.0f}" if m["fc"] else ""
        lineas.append(f"| {mes} | {m['n']} | {m['km']:.1f} | {m['desnivel']:.0f} | {seg_a_hms(m['seg'])} "
                      f"| {fc} | {m['carga']:.0f} |")

    lineas += tabla_markdown(
        "Detalle de actividades", lista,
        ["fecha", "nombre", "tipo", "distancia_km", "tiempo_movimiento", "velocidad_media_kmh",
         "desnivel_positivo_m", "fc_media", "fc_max", "cadencia_media_rpm", "potencia_media_w",
         "efecto_aerobico", "tipo_entrenamiento", "carga_entrenamiento"])

    lineas += tabla_markdown(
        "Salidas con potencia", [a for a in lista if num(a["potencia_media_w"]) > 0],
        ["fecha", "nombre", "potencia_media_w", "potencia_normalizada_w", "potencia_max_w",
         "mejor_potencia_5s_w", "mejor_potencia_1min_w", "mejor_potencia_5min_w", "mejor_potencia_20min_w",
         "ftp_w", "if", "tss", "fc_media"])

    lineas += [
        "",
        "## Diccionario de columnas",
        "",
        "El fichero actividades_ciclismo.csv contiene todas estas columnas para cada actividad:",
        "",
        "- id: identificador de la actividad en Garmin Connect",
        "- fecha, hora_inicio: inicio de la actividad (hora local)",
        "- nombre, tipo, lugar: nombre de la salida, tipo de Garmin y localidad de inicio",
    ]
    lineas += [f"- {columna}: {descripcion}" for columna, _, _, descripcion in CAMPOS]
    MD_FILE.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def tabla_markdown(titulo, filas, cols):
    lineas = [
        "",
        f"## {titulo}",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for a in filas:
        lineas.append("| " + " | ".join(str(a.get(c, "")).replace("|", "/") for c in cols) + " |")

    return lineas


def importar(api, desde, hasta):
    log.info("Buscando actividades entre %s y %s", desde, hasta)
    actividades = api.get_activities_by_date(desde.isoformat(), hasta.isoformat())
    ciclismo = [a for a in actividades if es_ciclismo(a)]

    existentes = cargar_existentes()
    nuevas = 0
    for act in ciclismo:
        resumen = resumir(act)
        if resumen["id"] not in existentes:
            nuevas += 1
            log.info("Nueva: %s %s (%s km)", resumen["fecha"], resumen["nombre"], resumen["distancia_km"])
        existentes[resumen["id"]] = resumen

    escribir_salidas(existentes)
    log.info("%d actividades de ciclismo en el rango, %d nuevas. Total guardado: %d",
             len(ciclismo), nuevas, len(existentes))


def git(*args):
    return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True, text=True, encoding="utf-8")


def publicar():
    """Hace commit y push de la carpeta de datos a GitHub si ha cambiado."""
    git("add", str(SALIDA_DIR))
    if git("diff", "--cached", "--quiet").returncode == 0:
        log.info("Sin cambios que publicar")
        return

    git("commit", "-m", f"Actividades actualizadas {date.today().isoformat()}")
    resultado = git("push")
    if resultado.returncode != 0:
        log.error("Error al hacer push: %s", resultado.stderr.strip())
        sys.exit(1)

    log.info("Datos publicados en GitHub")


def parse_fecha(texto):
    return datetime.strptime(texto, "%Y-%m-%d").date()


def main():
    parser = argparse.ArgumentParser(description="Importa actividades de ciclismo de Garmin Connect")
    parser.add_argument("--login", action="store_true", help="Inicia sesión de forma interactiva y guarda los tokens")
    parser.add_argument("--desde", type=parse_fecha, help="Fecha inicial YYYY-MM-DD (importación histórica)")
    parser.add_argument("--hasta", type=parse_fecha, help="Fecha final YYYY-MM-DD (por defecto, ayer)")
    parser.add_argument("--publicar", action="store_true", help="Hace commit y push de los datos a GitHub")
    args = parser.parse_args()

    configurar_log()
    api = conectar(interactivo=args.login or sys.stdin.isatty())

    if args.login and not args.desde:
        log.info("Login correcto. Ya puedes importar con --desde AAAA-MM-DD")
        return

    ayer = date.today() - timedelta(days=1)
    estado = cargar_estado()
    hasta = args.hasta or ayer

    if args.desde:
        desde = args.desde
    elif "ultima_revision" in estado:
        # Si el PC estuvo apagado algún día, recupera los días pendientes
        desde = min(parse_fecha(estado["ultima_revision"]) + timedelta(days=1), ayer)
    else:
        desde = ayer

    importar(api, desde, hasta)

    if hasta >= parse_fecha(estado.get("ultima_revision", "1900-01-01")):
        estado["ultima_revision"] = hasta.isoformat()
        guardar_estado(estado)

    if args.publicar:
        publicar()


if __name__ == "__main__":
    main()
