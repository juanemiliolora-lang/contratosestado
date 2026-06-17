"""
server.py — Dashboard SEACE con extracción automática
- Descarga todos los Bienes Vigentes de Lima de SEACE
- Filtra por keywords de CIIUs configurados
- Cachea en data/resultados.json
- Scheduler: actualiza a las 8:00am y 2:00pm hora Lima (UTC-5)
- Endpoints: / (dashboard), /data (cache JSON), /refresh (forzar update), /status

Uso local:    python server.py
Deploy Render: Procfile → web: python server.py
"""

import http.server
import urllib.request
import urllib.parse
import json
import os
import time
import threading
import webbrowser
import sys
from datetime import datetime, timezone, timedelta

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────

PORT      = int(os.environ.get("PORT", 8080))
IS_LOCAL  = PORT == 8080
SEACE_API = "https://prod6.seace.gob.pe/v1/s8uit-services/buscadorpublico"
THIS_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(THIS_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "resultados.json")

LIMA_TZ   = timezone(timedelta(hours=-5))
HORAS_ACTUALIZACION = [8, 14]   # 8:00am y 2:00pm hora Lima

# ─── CIIU → KEYWORDS ─────────────────────────────────────────────────────────
# La búsqueda funciona al revés: descargamos TODOS los Bienes Vigentes Lima
# y filtramos localmente por estas keywords en la descripción.

CIIU_KEYWORDS = {
    "4659": [
        "generador", "grupo electrógeno", "transformador", "motor eléctrico",
        "bomba de agua", "compresor", "ventilador industrial", "equipo de soldadura",
        "montacargas", "maquinaria agrícola", "tractor", "podadora",
        "cocina industrial", "horno industrial", "cámara frigorífica",
        "equipo topográfico", "teodolito", "balanza industrial", "caudalímetro",
        "equipo industrial", "maquinaria",
    ],
    "4651": [
        "computadora", "laptop", "tablet", "impresora", "tóner", "toner",
        "cartucho", "mouse", "teclado", "monitor", "ups", "disco duro",
        "memoria ram", "switch", "router", "cable de red", "servidor",
        "proyector", "escáner", "scanner", "multifuncional", "disco ssd",
        "access point", "cámara ip", "licencia software", "antivirus",
    ],
    "4641": [
        "uniforme", "ropa de trabajo", "calzado de seguridad", "botas",
        "camisas", "pantalones", "casaca", "chaleco", "gorro",
        "overol", "buzo", "zapatos de seguridad", "tela",
        "ropa deportiva", "indumentaria", "vestimenta", "prendas de vestir",
    ],
    "4631": [
        "frutas", "verduras", "hortalizas", "víveres", "papa", "cebolla",
        "tomate", "lechuga", "zanahoria", "plátano", "naranja", "manzana",
        "limón", "yuca", "camote", "legumbres", "tubérculos", "vegetales",
    ],
    "4663": [
        "cemento", "fierro", "varilla", "alambre", "clavo", "tornillo",
        "pintura", "tubería", "tubo pvc", "válvula", "cable eléctrico",
        "interruptor", "tomacorriente", "foco led", "reflector", "escalera",
        "manguera", "taladro", "esmeril", "disco de corte",
        "casco de seguridad", "extintor", "señalización", "andamio",
        "grifería", "sanitario", "inodoro", "lavatorio", "ferretería",
    ],
    "4661": [
        "combustible", "petróleo", "gasolina", "diesel", "gas", "glp",
        "lubricante", "aceite de motor", "aceite hidráulico", "grasa",
        "refrigerante", "aditivo",
    ],
    "4645": [
        "medicamento", "medicina", "tableta", "cápsula", "jarabe", "ampolla",
        "suero", "antibiótico", "analgésico", "vitamina", "alcohol medicinal",
        "gasa", "venda", "jeringa", "guantes quirúrgicos", "mascarilla",
        "termómetro", "tensiómetro", "oxímetro", "glucómetro",
        "botiquín", "reactivo", "insumo médico",
    ],
    "4649": [
        "mueble", "silla", "escritorio", "mesa", "armario", "estante",
        "archivador", "sillón", "colchón", "frazada", "cortina",
        "vajilla", "olla", "refrigeradora", "ventilador", "aire acondicionado",
        "lámpara", "escoba", "trapeador", "detergente", "desinfectante",
        "útiles de limpieza", "artículos de oficina", "menaje",
    ],
    "3100": [
        "mueble de madera", "escritorio de madera", "estante de madera",
        "armario de madera", "módulo de oficina", "biombo", "tabique",
        "carpintería", "mobiliario", "librero", "vitrina",
    ],
}

# Índice inverso keyword → [ciiús] para búsqueda rápida
_KW_INDEX = {}
for ciiu, kws in CIIU_KEYWORDS.items():
    for kw in kws:
        _KW_INDEX.setdefault(kw.lower(), []).append(ciiu)

# ─── SCRAPE JOB ───────────────────────────────────────────────────────────────

_scrape_lock   = threading.Lock()
_scrape_status = {"running": False, "last_run": None, "error": None, "total_raw": 0, "total_filtrado": 0}


def clasificar_item(descripcion: str) -> tuple[list, list]:
    """Retorna (ciiús_match, keywords_match) para una descripción."""
    desc = (descripcion or "").lower()
    ciiuMatch = set()
    kwMatch   = []
    for kw, ciiuList in _KW_INDEX.items():
        if kw in desc:
            kwMatch.append(kw)
            ciiuMatch.update(ciiuList)
    return sorted(ciiuMatch), kwMatch


def fetch_pagina_seace(params: dict) -> dict:
    url    = f"{SEACE_API}/contrataciones/buscador"
    qs     = urllib.parse.urlencode(params)
    req    = urllib.request.Request(f"{url}?{qs}", headers={"User-Agent": "SEACE-Dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def ejecutar_scrape():
    """Descarga todos los Bienes Vigentes de Lima y filtra por CIIUs."""
    if not _scrape_lock.acquire(blocking=False):
        print("[Scrape] Ya hay una ejecución en curso, se omite.")
        return

    _scrape_status["running"] = True
    _scrape_status["error"]   = None
    inicio = datetime.now(LIMA_TZ)
    print(f"[Scrape] Iniciando — {inicio.strftime('%d/%m/%Y %H:%M')} hora Lima")

    try:
        todos_raw  = {}   # idContrato → item (dedup)
        page_size  = 50
        page       = 1
        total_pag  = 1    # se actualiza en la primera respuesta
        anio       = datetime.now(LIMA_TZ).year

        while page <= total_pag:
            params = {
                "anio":                  anio,
                "lista_codigo_objeto":   "1",   # Bien
                "lista_estado_contrato": "2",   # Vigente
                "codigo_departamento":   "15",  # Lima
                "palabra_clave":         "",
                "campo_orden":           "1",
                "orden":                 "2",
                "page":                  page,
                "page_size":             page_size,
            }
            data    = fetch_pagina_seace(params)
            items   = data.get("data", [])
            pag     = data.get("pageable", {})
            total   = pag.get("totalElements", 0)
            total_pag = max(1, -(-total // page_size))   # ceil division

            for item in items:
                todos_raw[item["idContrato"]] = item

            print(f"  Página {page}/{total_pag} — {len(items)} items — Total SEACE: {total}")
            page += 1
            time.sleep(0.3)   # cortesía al servidor

        # Clasificar por CIIU
        filtrados = []
        for item in todos_raw.values():
            desc = item.get("desObjetoContrato", "")
            ciiuMatch, kwMatch = clasificar_item(desc)
            if ciiuMatch:
                item["ciiuMatch"]    = ciiuMatch
                item["keywordMatch"] = kwMatch
                filtrados.append(item)

        # Guardar caché
        os.makedirs(DATA_DIR, exist_ok=True)
        cache = {
            "updated_at":      inicio.isoformat(),
            "anio":            anio,
            "total_raw":       len(todos_raw),
            "total_filtrado":  len(filtrados),
            "items":           filtrados,
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)

        _scrape_status["last_run"]       = inicio.isoformat()
        _scrape_status["total_raw"]      = len(todos_raw)
        _scrape_status["total_filtrado"] = len(filtrados)
        print(f"[Scrape] OK — {len(todos_raw)} Bienes Lima → {len(filtrados)} con CIIU match")

    except Exception as e:
        _scrape_status["error"] = str(e)
        print(f"[Scrape] ERROR: {e}", file=sys.stderr)
    finally:
        _scrape_status["running"] = False
        _scrape_lock.release()


# ─── SCHEDULER ────────────────────────────────────────────────────────────────

def scheduler_loop():
    """Revisa cada minuto si es hora de actualizar (8:00am o 2:00pm Lima)."""
    ultimo_disparo = None
    while True:
        ahora_lima = datetime.now(LIMA_TZ)
        clave = (ahora_lima.date(), ahora_lima.hour)

        if ahora_lima.hour in HORAS_ACTUALIZACION and ahora_lima.minute == 0:
            if clave != ultimo_disparo:
                ultimo_disparo = clave
                print(f"[Scheduler] Hora programada ({ahora_lima.hour}:00 Lima) — lanzando scrape")
                threading.Thread(target=ejecutar_scrape, daemon=True).start()

        time.sleep(30)   # chequea cada 30 segundos


# ─── SERVIDOR HTTP ────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        ruta = self.path.split("?")[0]
        if ruta in ("/data", "/refresh", "/status", "/"):
            print(f"  {self.command} {ruta} → {args[1]}")

    # ── GET ────────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path   = parsed.path

        # /data → caché JSON
        if path == "/data":
            if os.path.isfile(DATA_FILE):
                with open(DATA_FILE, "rb") as f:
                    body = f.read()
                self._send(200, "application/json", body)
            else:
                self._send(404, "application/json", b'{"error":"Sin datos. Espera la primera ejecución del scrape."}')
            return

        # /status → estado del scrape
        if path == "/status":
            body = json.dumps(_scrape_status, default=str).encode()
            self._send(200, "application/json", body)
            return

        # / → dashboard HTML
        if path == "/" or path == "/index.html":
            filepath = os.path.join(THIS_DIR, "dashboard_seace.html")
        else:
            filepath = os.path.join(THIS_DIR, path.lstrip("/"))

        if os.path.isfile(filepath):
            ext = os.path.splitext(filepath)[1]
            ct  = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
                   ".css": "text/css", ".json": "application/json"}.get(ext, "application/octet-stream")
            with open(filepath, "rb") as f:
                body = f.read()
            self._send(200, ct, body)
        else:
            self._send(404, "text/plain", b"Not found")

    # ── POST ───────────────────────────────────────────────────────────────────
    def do_POST(self):
        if self.path == "/refresh":
            if _scrape_status["running"]:
                self._send(409, "application/json", b'{"status":"already_running"}')
            else:
                threading.Thread(target=ejecutar_scrape, daemon=True).start()
                self._send(202, "application/json", b'{"status":"started"}')
        else:
            self._send(404, "text/plain", b"Not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _send(self, code, ct, body):
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


# ─── ARRANQUE ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.chdir(THIS_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)

    server = http.server.ThreadingHTTPServer(("", PORT), Handler)

    # Scheduler en background
    threading.Thread(target=scheduler_loop, daemon=True).start()

    # Primera carga al arrancar (si no hay caché)
    if not os.path.isfile(DATA_FILE):
        print("[Inicio] No hay caché — ejecutando primer scrape...")
        threading.Thread(target=ejecutar_scrape, daemon=True).start()
    else:
        with open(DATA_FILE) as f:
            meta = json.load(f)
        print(f"[Inicio] Caché existente: {meta.get('total_filtrado')} oportunidades, actualizado {meta.get('updated_at')}")

    if IS_LOCAL:
        print(f"\n  Dashboard: http://localhost:{PORT}")
        print(f"  Datos:     http://localhost:{PORT}/data")
        print(f"  Estado:    http://localhost:{PORT}/status")
        print(f"  Actualizar: POST http://localhost:{PORT}/refresh\n")
        threading.Thread(target=lambda: (time.sleep(1), webbrowser.open(f"http://localhost:{PORT}")), daemon=True).start()
    else:
        print(f"[Render] Servidor en puerto {PORT} — Scheduler activo (8:00 y 14:00 Lima)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Servidor detenido]")
