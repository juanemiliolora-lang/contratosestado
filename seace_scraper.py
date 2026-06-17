"""
seace_scraper.py
Extrae contrataciones <8 UIT de SEACE con filtros configurables.
Compatible con n8n (HTTP Request node), Claude Code y ejecución directa.

Uso:
    python seace_scraper.py                          # Bienes + Vigente + Lima
    python seace_scraper.py --objeto 2               # Servicios
    python seace_scraper.py --keyword "laptop"       # Con palabra clave
    python seace_scraper.py --ciiu 4651              # Filtro por CIIU
    python seace_scraper.py --todas-paginas          # Extrae todo
    python seace_scraper.py --output resultados.csv  # Exportar CSV

Requiere:
    pip install requests pandas
"""

import requests
import pandas as pd
import argparse
import json
import sys
from datetime import datetime
from typing import Optional

# ─── CONFIGURACIÓN BASE ────────────────────────────────────────────────────────

API_BASE = "https://prod6.seace.gob.pe/v1/s8uit-services/buscadorpublico"

# Mapeo de IDs de maestras
OBJETOS = {"Bien": "1", "Servicio": "2", "Obra": "3", "Consultoría de Obra": "4"}
ESTADOS = {"Vigente": "2", "En Evaluación": "3", "Culminado": "4"}
DEPARTAMENTOS = {
    "LIMA": "15", "AREQUIPA": "4", "CUSCO": "8", "CALLAO": "7",
    "LA LIBERTAD": "13", "PIURA": "20", "JUNIN": "12", "LAMBAYEQUE": "14",
    "ANCASH": "2", "ICA": "11", "LORETO": "16", "SAN MARTIN": "22",
    "PUNO": "21", "CAJAMARCA": "6", "AYACUCHO": "5",
}

# Mapeo CIIU → palabras clave para búsqueda en SEACE
# SEACE no filtra por CIIU directamente — se busca por palabra_clave en la descripción
CIIU_KEYWORDS = {
    "4659": [
        # Maquinaria y equipo NCP
        "generador", "grupo electrógeno", "transformador", "motor eléctrico",
        "bomba de agua", "compresor", "ventilador industrial", "equipo de soldadura",
        "montacargas", "maquinaria agrícola", "tractor", "podadora",
        "cocina industrial", "horno industrial", "cámara frigorífica",
        "equipo topográfico", "teodolito", "balanza industrial", "caudalímetro",
        "equipo de medición", "maquinaria", "equipo industrial",
    ],
    "4651": [
        # Informática y periféricos
        "computadora", "laptop", "tablet", "impresora", "tóner", "toner",
        "cartucho", "mouse", "teclado", "monitor", "UPS", "disco duro",
        "memoria RAM", "switch", "router", "cable de red", "servidor",
        "proyector", "escáner", "multifuncional", "disco SSD",
        "access point", "cámara IP", "licencia software", "antivirus",
    ],
    "4641": [
        # Textiles, ropa y calzado
        "uniforme", "ropa de trabajo", "calzado de seguridad", "botas",
        "camisas", "pantalones", "casaca", "chaleco", "gorro",
        "overol", "buzo", "zapatos de seguridad", "tela",
        "ropa deportiva", "indumentaria", "vestimenta", "prendas de vestir",
    ],
    "4631": [
        # Frutas, verduras y víveres
        "frutas", "verduras", "hortalizas", "víveres", "papa", "cebolla",
        "tomate", "lechuga", "zanahoria", "plátano", "naranja", "manzana",
        "limón", "yuca", "camote", "legumbres", "tubérculos", "vegetales",
    ],
    "4663": [
        # Ferretería, construcción y plomería
        "cemento", "fierro", "varilla de acero", "alambre", "clavo", "tornillo",
        "pintura", "tubería", "tubo PVC", "válvula", "cable eléctrico",
        "interruptor", "tomacorriente", "foco LED", "reflector", "escalera",
        "manguera", "taladro", "esmeril", "disco de corte",
        "casco de seguridad", "extintor", "señalización", "andamio",
        "grifería", "sanitario", "inodoro", "lavatorio", "ferretería",
    ],
    "4661": [
        # Combustibles y lubricantes
        "combustible", "petróleo", "gasolina", "diesel", "gas", "GLP",
        "lubricante", "aceite de motor", "aceite hidráulico", "grasa",
        "refrigerante", "aditivo",
    ],
    "4645": [
        # Farmacéutico y médico
        "medicamento", "medicina", "tableta", "cápsula", "jarabe", "ampolla",
        "suero", "antibiótico", "analgésico", "vitamina", "alcohol medicinal",
        "gasa", "venda", "jeringa", "guantes quirúrgicos", "mascarilla",
        "termómetro", "tensiómetro", "oxímetro", "glucómetro",
        "botiquín", "reactivo", "insumo médico",
    ],
    "4649": [
        # Enseres domésticos y artículos de oficina
        "mueble", "silla", "escritorio", "mesa", "armario", "estante",
        "archivador", "sillón", "colchón", "frazada", "cortina",
        "vajilla", "olla", "refrigeradora", "ventilador", "aire acondicionado",
        "lámpara", "escoba", "trapeador", "detergente", "desinfectante",
        "útiles de limpieza", "artículos de oficina", "menaje",
    ],
    "3100": [
        # Fabricación de muebles (complementa 4659)
        "mueble de madera", "escritorio de madera", "estante de madera",
        "armario de madera", "módulo de oficina", "biombo", "tabique",
        "carpintería", "mobiliario", "librero", "vitrina",
    ],
}

CIIUS_OBJETIVO = ["4659", "4651", "4641", "4631", "4663", "4661", "4645", "4649", "3100"]


# ─── CLIENTE API ───────────────────────────────────────────────────────────────

def construir_params(
    anio: int = None,
    objeto: str = "1",       # 1=Bien
    estado: str = "2",       # 2=Vigente
    departamento: str = "15", # 15=Lima
    keyword: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    params = {
        "anio": anio or datetime.now().year,
        "lista_codigo_objeto": objeto,
        "lista_estado_contrato": estado,
        "codigo_departamento": departamento,
        "palabra_clave": keyword,
        "campo_orden": "1",   # por fecha publicación
        "orden": "2",          # DESC
        "page": page,
        "page_size": page_size,
    }
    # Omitir vacíos para no sobre-filtrar
    return {k: v for k, v in params.items() if v != ""}


def fetch_pagina(params: dict, session: requests.Session) -> dict:
    url = f"{API_BASE}/contrataciones/buscador"
    try:
        resp = session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return {}


def fetch_todas_las_paginas(params: dict, max_paginas: int = 20) -> list[dict]:
    """Extrae todas las páginas hasta max_paginas."""
    session = requests.Session()
    session.headers.update({"User-Agent": "SEACE-Dashboard/1.0"})

    todos = []
    page = 1

    while page <= max_paginas:
        params["page"] = page
        data = fetch_pagina(params, session)
        registros = data.get("data", [])
        paginable = data.get("pageable", {})

        if not registros:
            break

        todos.extend(registros)
        total = paginable.get("totalElements", 0)
        ps    = paginable.get("pageSize", 50)
        total_pages = (total + ps - 1) // ps

        print(f"  Página {page}/{total_pages} — {len(registros)} registros — Total: {total}")

        if page >= total_pages:
            break
        page += 1

    return todos


# ─── EXTRACCIÓN POR CIIU ────────────────────────────────────────────────────

def extraer_por_ciiu(
    ciius: list[str],
    anio: int = None,
    departamento: str = "15",
    estado: str = "2",
    todas_paginas: bool = False,
) -> pd.DataFrame:
    """
    Extrae oportunidades para cada CIIU usando sus palabras clave.
    Devuelve un DataFrame consolidado sin duplicados.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": "SEACE-Dashboard/1.0"})
    todos = {}

    for ciiu in ciius:
        keywords = CIIU_KEYWORDS.get(ciiu, [])
        print(f"\n[CIIU {ciiu}] Buscando {len(keywords)} keywords...")

        for kw in keywords:
            params = construir_params(
                anio=anio,
                objeto="1",
                estado=estado,
                departamento=departamento,
                keyword=kw,
                page_size=50,
            )

            if todas_paginas:
                registros = fetch_todas_las_paginas(params)
            else:
                data = fetch_pagina(params, session)
                registros = data.get("data", [])
                total = data.get("pageable", {}).get("totalElements", 0)
                print(f"  '{kw}': {len(registros)} de {total}")

            for r in registros:
                r["ciiu_match"] = ciiu
                r["keyword_match"] = kw
                todos[r["idContrato"]] = r  # deduplica por ID

    return pd.DataFrame(list(todos.values()))


# ─── PROCESAMIENTO ─────────────────────────────────────────────────────────────

def procesar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia y enriquece el DataFrame."""
    if df.empty:
        return df

    # Parsear fechas
    def parse_fecha(s):
        if not s:
            return None
        try:
            return datetime.strptime(str(s), "%d/%m/%Y %H:%M:%S")
        except:
            return None

    df["fecha_publicacion"] = df["fecPublica"].apply(parse_fecha)
    df["fecha_cierre"]      = df["fecFinCotizacion"].apply(parse_fecha)
    df["fecha_inicio"]      = df["fecIniCotizacion"].apply(parse_fecha)

    # Horas restantes para cotizar
    ahora = datetime.now()
    df["horas_restantes"] = df["fecha_cierre"].apply(
        lambda x: round((x - ahora).total_seconds() / 3600, 1) if x else None
    )
    df["urgente"] = df["horas_restantes"].apply(
        lambda h: True if h is not None and 0 < h < 24 else False
    )

    # Columnas limpias para exportar
    df = df.rename(columns={
        "idContrato":         "id_contrato",
        "desContratacion":    "nro_contrato",
        "nomEntidad":         "entidad",
        "nomObjetoContrato":  "objeto",
        "nomEstadoContrato":  "estado",
        "desObjetoContrato":  "descripcion",
    })

    cols_orden = [
        "id_contrato", "nro_contrato", "entidad", "objeto", "estado",
        "descripcion", "fecha_publicacion", "fecha_inicio", "fecha_cierre",
        "horas_restantes", "urgente",
    ]
    if "ciiu_match" in df.columns:
        cols_orden += ["ciiu_match", "keyword_match"]

    cols_disponibles = [c for c in cols_orden if c in df.columns]
    return df[cols_disponibles].sort_values("horas_restantes", ascending=True, na_position="last")


# ─── REPORTE ───────────────────────────────────────────────────────────────────

def imprimir_resumen(df: pd.DataFrame):
    print(f"\n{'='*60}")
    print(f"  RESUMEN — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*60}")
    print(f"  Total oportunidades: {len(df)}")

    if "urgente" in df.columns:
        urgentes = df["urgente"].sum()
        print(f"  Cierran en <24h:     {urgentes}")

    if "ciiu_match" in df.columns:
        print(f"\n  Por CIIU:")
        for ciiu, cnt in df["ciiu_match"].value_counts().items():
            print(f"    {ciiu}: {cnt} oportunidades")

    if "entidad" in df.columns and len(df) > 0:
        print(f"\n  Top 5 entidades:")
        for ent, cnt in df["entidad"].value_counts().head(5).items():
            print(f"    {cnt:3d}  {ent[:60]}")

    print(f"{'='*60}\n")


def exportar(df: pd.DataFrame, output: str):
    if output.endswith(".xlsx"):
        df.to_excel(output, index=False)
    elif output.endswith(".json"):
        df.to_json(output, orient="records", force_ascii=False, indent=2)
    else:
        df.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"[OK] Exportado: {output} ({len(df)} filas)")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extrae contrataciones <8 UIT de SEACE"
    )
    parser.add_argument("--anio",          type=int,   default=None,  help="Año (default: año actual)")
    parser.add_argument("--objeto",        type=str,   default="1",   help="Código objeto: 1=Bien, 2=Servicio")
    parser.add_argument("--estado",        type=str,   default="2",   help="Código estado: 2=Vigente, 3=En Evaluación, 4=Culminado")
    parser.add_argument("--departamento",  type=str,   default="15",  help="Código departamento INEI (15=Lima)")
    parser.add_argument("--keyword",       type=str,   default="",    help="Palabra clave libre")
    parser.add_argument("--ciiu",          type=str,   default=None,  help="CIIU específico (ej: 4651). Si omites, usa todos los CIIU objetivo.")
    parser.add_argument("--todos-ciiu",    action="store_true",       help="Buscar por todos los CIIUs configurados")
    parser.add_argument("--todas-paginas", action="store_true",       help="Extraer todas las páginas (puede ser lento)")
    parser.add_argument("--page-size",     type=int,   default=50,    help="Registros por página")
    parser.add_argument("--output",        type=str,   default=None,  help="Archivo salida: .csv / .xlsx / .json")
    parser.add_argument("--json",          action="store_true",       help="Imprimir resultado como JSON (para n8n)")

    args = parser.parse_args()

    # ── Modo CIIU ──────────────────────────────────────────────────────────
    if args.todos_ciiu or args.ciiu:
        ciius = [args.ciiu] if args.ciiu else CIIUS_OBJETIVO
        print(f"[MODO CIIU] CIIUs: {ciius}")
        df_raw = extraer_por_ciiu(
            ciius=ciius,
            anio=args.anio,
            departamento=args.departamento,
            estado=args.estado,
            todas_paginas=args.todas_paginas,
        )

    # ── Modo búsqueda simple ────────────────────────────────────────────────
    else:
        session = requests.Session()
        session.headers.update({"User-Agent": "SEACE-Dashboard/1.0"})
        params = construir_params(
            anio=args.anio,
            objeto=args.objeto,
            estado=args.estado,
            departamento=args.departamento,
            keyword=args.keyword,
            page_size=args.page_size,
        )

        if args.todas_paginas:
            registros = fetch_todas_las_paginas(params)
        else:
            data = fetch_pagina(params, session)
            registros = data.get("data", [])
            total = data.get("pageable", {}).get("totalElements", 0)
            print(f"[OK] {len(registros)} de {total} registros (página 1)")

        df_raw = pd.DataFrame(registros)

    # ── Procesar ────────────────────────────────────────────────────────────
    if df_raw.empty:
        print("[SIN RESULTADOS]")
        sys.exit(0)

    df = procesar_df(df_raw)

    # ── Salida ──────────────────────────────────────────────────────────────
    if args.json:
        # Formato JSON para n8n: devuelve array de objetos
        print(df.to_json(orient="records", force_ascii=False, date_format="iso"))
    else:
        imprimir_resumen(df)

    if args.output:
        exportar(df, args.output)
    elif not args.json:
        print(df.to_string(max_rows=20, max_colwidth=60))


if __name__ == "__main__":
    main()
