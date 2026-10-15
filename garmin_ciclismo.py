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

COLUMNAS = [
    "id", "fecha", "hora_inicio", "nombre", "tipo", "lugar",
    "distancia_km", "duracion", "tiempo_movimiento",
    "velocidad_media_kmh", "velocidad_max_kmh",
    "desnivel_positivo_m", "desnivel_negativo_m", "altitud_max_m",
    "fc_media", "fc_max",
    "potencia_media_w", "potencia_normalizada_w", "potencia_max_w",
    "cadencia_media_rpm", "calorias",
    "efecto_aerobico", "efecto_anaerobico", "carga_entrenamiento",
    "tss", "if", "temperatura_media_c",
]

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


def resumir(act):
    inicio = act.get("startTimeLocal", "")
    return {
        "id": act.get("activityId"),
        "fecha": inicio[:10],
        "hora_inicio": inicio[11:16],
        "nombre": act.get("activityName", ""),
        "tipo": (act.get("activityType") or {}).get("typeKey", ""),
        "lugar": act.get("locationName", "") or "",
        "distancia_km": redondear(act.get("distance"), 2, 0.001),
        "duracion": seg_a_hms(act.get("duration")),
        "tiempo_movimiento": seg_a_hms(act.get("movingDuration")),
        "velocidad_media_kmh": redondear(act.get("averageSpeed"), 1, 3.6),
        "velocidad_max_kmh": redondear(act.get("maxSpeed"), 1, 3.6),
        "desnivel_positivo_m": redondear(act.get("elevationGain"), 0),
        "desnivel_negativo_m": redondear(act.get("elevationLoss"), 0),
        "altitud_max_m": redondear(act.get("maxElevation"), 0),
        "fc_media": redondear(act.get("averageHR"), 0),
        "fc_max": redondear(act.get("maxHR"), 0),
        "potencia_media_w": redondear(act.get("avgPower"), 0),
        "potencia_normalizada_w": redondear(act.get("normPower"), 0),
        "potencia_max_w": redondear(act.get("maxPower"), 0),
        "cadencia_media_rpm": redondear(act.get("averageBikingCadenceInRevPerMinute"), 0),
        "calorias": redondear(act.get("calories"), 0),
        "efecto_aerobico": redondear(act.get("aerobicTrainingEffect"), 1),
        "efecto_anaerobico": redondear(act.get("anaerobicTrainingEffect"), 1),
        "carga_entrenamiento": redondear(act.get("activityTrainingLoad"), 0),
        "tss": redondear(act.get("trainingStressScore"), 0),
        "if": redondear(act.get("intensityFactor"), 2),
        "temperatura_media_c": redondear(act.get("averageTemperature"), 0),
    }


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

    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)

    # utf-8-sig para que Excel también lo abra bien
    with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(lista)

    escribir_markdown(lista)


def escribir_markdown(lista):
    meses = defaultdict(lambda: {"n": 0, "km": 0.0, "desnivel": 0.0, "seg": 0})
    for a in lista:
        m = meses[a["fecha"][:7]]
        m["n"] += 1
        m["km"] += num(a["distancia_km"])
        m["desnivel"] += num(a["desnivel_positivo_m"])
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
        "| Mes | Salidas | Km | Desnivel + (m) | Tiempo en movimiento |",
        "|---|---|---|---|---|",
    ]
    for mes in sorted(meses, reverse=True):
        m = meses[mes]
        lineas.append(f"| {mes} | {m['n']} | {m['km']:.1f} | {m['desnivel']:.0f} | {seg_a_hms(m['seg'])} |")

    cols = ["fecha", "nombre", "tipo", "distancia_km", "tiempo_movimiento", "velocidad_media_kmh",
            "desnivel_positivo_m", "fc_media", "potencia_media_w", "potencia_normalizada_w", "tss"]
    lineas += [
        "",
        "## Detalle de actividades",
        "",
        "| " + " | ".join(cols) + " |",
        "|" + "---|" * len(cols),
    ]
    for a in lista:
        lineas.append("| " + " | ".join(str(a[c]).replace("|", "/") for c in cols) + " |")

    lineas += ["", "El fichero actividades_ciclismo.csv contiene todas las columnas de cada actividad."]
    MD_FILE.write_text("\n".join(lineas) + "\n", encoding="utf-8")


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
