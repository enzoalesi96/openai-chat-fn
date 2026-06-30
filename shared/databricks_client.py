"""
shared/databricks_client.py
----------------------------
Interacciones con Databricks:
  1. Invocar el Model Serving endpoint (visionmatch-kmeans) para obtener el cluster_id.
  2. Consultar la tabla Delta Lake directamente via SQL Statement API
     para recuperar el top-5 de candidatos del cluster del usuario.
"""

import os
import re
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

DATABRICKS_TOKEN   = os.environ["DATABRICKS_TOKEN"]

# Endpoint de Model Serving (ya desplegado, no se necesita Job)
SERVING_ENDPOINT_URL = (
    "https://adb-7405610080077220.0.azuredatabricks.net"
    "/serving-endpoints/visionmatch-kmeans/invocations"
)

# Tabla fuente en Delta Lake
SOURCE_TABLE = "adbvisualmatch_7405610080077220.schema_tv.data_oh_completa"

# Mapeo familia → numérico (igual que modelo_televisores.py)
FAMILY_NUM: dict[str, float] = {
    "LED":      0.0,
    "QLED":     1.0,
    "OLED":     2.0,
    "NanoCell": 3.0,
}

_AUTH_HEADERS = {
    "Authorization": f"Bearer {DATABRICKS_TOKEN}",
    "Content-Type":  "application/json",
}


# ---------------------------------------------------------------------------
# 1. Invocar el Model Serving endpoint → cluster_id
# ---------------------------------------------------------------------------

def predict_cluster(pulgadas: int, presupuesto: float, familia: str) -> int:
    """
    Llama al endpoint visionmatch-kmeans con el perfil del usuario
    y devuelve el cluster_id asignado por el modelo KMeans.
    """
    familia_num = FAMILY_NUM.get(familia, 0.0)

    payload = {
        "dataframe_records": [
            {
                "pulgadas":    float(pulgadas),
                "presupuesto": float(presupuesto),
                "familia_num": familia_num,
            }
        ]
    }

    logger.info(
        "🤖 Invocando Model Serving endpoint: pulgadas=%s, presupuesto=%s, familia=%s (%.1f)",
        pulgadas, presupuesto, familia, familia_num,
    )

    # El endpoint puede tener scale_to_zero activado: la primera llamada
    # tras un periodo inactivo dispara un cold start de 2-5 min. Reintentamos
    # con un timeout amplio para tolerarlo.
    last_exc = None
    for intento in range(1, 4):
        try:
            resp = requests.post(
                SERVING_ENDPOINT_URL,
                headers=_AUTH_HEADERS,
                json=payload,
                timeout=180,   # 3 min para tolerar cold start
            )
            resp.raise_for_status()
            data = resp.json()
            cluster_id = int(data["predictions"][0])
            logger.info("✅ Cluster asignado: %d (intento %d)", cluster_id, intento)
            return cluster_id
        except requests.exceptions.ReadTimeout as exc:
            last_exc = exc
            logger.warning("⏳ Timeout en intento %d (posible cold start), reintentando...", intento)

    raise RuntimeError(
        "El endpoint de Model Serving no respondió a tiempo. "
        "Puede estar arrancando (cold start). Intenta de nuevo en unos minutos. "
        f"Detalle: {last_exc}"
    )


# ---------------------------------------------------------------------------
# 2. Consultar candidatos en Delta Lake via SQL Statement API
# ---------------------------------------------------------------------------

def fetch_top5(
    cluster_id: int,
    pulgadas_ref: int,
    presupuesto: float,
    familia: str,
) -> list[dict]:
    """
    Consulta la tabla Delta Lake y devuelve el top-5 de televisores.

    Estrategia:
      1. Intenta filtrar por el cluster del usuario (familia_num == cluster_id).
      2. Si hay < 5 resultados, amplía a todo el catálogo dentro del presupuesto.

    Correcciones de formato de datos reales:
      - precios son STRING con coma de miles ("1,199.00") y vacíos como "--".
      - TRY_CAST + NULLIF para evitar que un precio inválido rompa la query.
      - LEAST con COALESCE para tomar el mínimo entre precios válidos.
      - pulgadas extraídas del patrón "-NN-" del nombre.
    """
    presupuesto_max = presupuesto * 1.10

    # CTE base reutilizable: limpia precios y extrae pulgadas + familia_num
    base_cte = f"""
    WITH precios_limpios AS (
      SELECT
        name, family, seller, url_product, url_image,
        TRY_CAST(NULLIF(REGEXP_REPLACE(internet_price, '[^0-9.]', ''), '') AS DOUBLE) AS p_internet,
        TRY_CAST(NULLIF(REGEXP_REPLACE(event_price,    '[^0-9.]', ''), '') AS DOUBLE) AS p_event,
        TRY_CAST(NULLIF(REGEXP_REPLACE(normal_price,   '[^0-9.]', ''), '') AS DOUBLE) AS p_normal,
        TRY_CAST(NULLIF(REGEXP_REPLACE(cmr_price,      '[^0-9.]', ''), '') AS DOUBLE) AS p_cmr,
        TRY_CAST(REGEXP_EXTRACT(name, '-([0-9]{{2,3}})-', 1) AS INT) AS pulgadas,
        CASE
          WHEN UPPER(family) LIKE '%OLED%'     THEN 2.0
          WHEN UPPER(family) LIKE '%QLED%'     THEN 1.0
          WHEN UPPER(family) LIKE '%NANOCELL%' THEN 3.0
          ELSE 0.0
        END AS familia_num
      FROM {SOURCE_TABLE}
    ),
    con_precio AS (
      SELECT
        name, family, seller, url_product, url_image, pulgadas, familia_num,
        LEAST(
          COALESCE(NULLIF(p_internet, 0), 999999),
          COALESCE(NULLIF(p_event,    0), 999999),
          COALESCE(NULLIF(p_normal,   0), 999999),
          COALESCE(NULLIF(p_cmr,      0), 999999)
        ) AS precio
      FROM precios_limpios
      WHERE pulgadas IS NOT NULL
    )
    """

    select_tail = f"""
    SELECT
      name,
      family AS familia,
      pulgadas,
      ROUND(precio, 2) AS precio,
      seller AS vendedor,
      url_product AS url,
      url_image AS imagen,
      ROUND((precio / {presupuesto}) * 100 - ABS(pulgadas - {pulgadas_ref}) * 2.0, 2) AS score
    FROM con_precio
    WHERE precio < 999999
      AND precio <= {presupuesto_max}
    """

    # 1) Intento con filtro de cluster
    sql_cluster = (
        base_cte
        + select_tail
        + f" AND familia_num = {float(cluster_id)} ORDER BY score DESC LIMIT 5"
    )
    rows = _run_sql(sql_cluster)

    # 2) Fallback: sin filtro de cluster, todo el catálogo en presupuesto
    if len(rows) < 5:
        logger.warning(
            "Solo %d candidatos en cluster %d. Ampliando a todo el catálogo...",
            len(rows), cluster_id,
        )
        sql_all = base_cte + select_tail + " ORDER BY score DESC LIMIT 5"
        rows = _run_sql(sql_all)

    return rows


# ---------------------------------------------------------------------------
# Helper: ejecutar SQL via Statement Execution API
# ---------------------------------------------------------------------------

_DATABRICKS_HOST = "https://adb-7405610080077220.0.azuredatabricks.net"
_warehouse_id_cache: Optional[str] = None


def _run_sql(sql: str) -> list[dict]:
    warehouse_id = _get_warehouse_id()

    stmt = _db_post("/api/2.0/sql/statements", {
        "statement":      sql,
        "warehouse_id":   warehouse_id,
        "wait_timeout":   "30s",
        "on_wait_timeout": "CONTINUE",
    })

    statement_id = stmt["statement_id"]
    state        = stmt["status"]["state"]

    for _ in range(15):
        if state in ("SUCCEEDED", "FAILED", "CANCELED", "CLOSED"):
            break
        time.sleep(3)
        stmt  = _db_get(f"/api/2.0/sql/statements/{statement_id}")
        state = stmt["status"]["state"]

    if state != "SUCCEEDED":
        # Extraer el mensaje de error real que devuelve Databricks
        error_info = stmt.get("status", {}).get("error", {})
        error_msg  = error_info.get("message", "sin detalle")
        raise RuntimeError(f"SQL statement '{state}': {error_msg}")

    col_names = [c["name"] for c in stmt["manifest"]["schema"]["columns"]]
    rows      = stmt.get("result", {}).get("data_array", [])
    return [dict(zip(col_names, row)) for row in rows]


def _db_post(path: str, payload: dict) -> dict:
    resp = requests.post(
        f"{_DATABRICKS_HOST}{path}",
        headers=_AUTH_HEADERS,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _db_get(path: str) -> dict:
    resp = requests.get(
        f"{_DATABRICKS_HOST}{path}",
        headers=_AUTH_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_warehouse_id() -> str:
    global _warehouse_id_cache
    if _warehouse_id_cache:
        return _warehouse_id_cache

    wh_env = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if wh_env:
        _warehouse_id_cache = wh_env
        return _warehouse_id_cache

    data    = _db_get("/api/2.0/sql/warehouses")
    wh_list = data.get("warehouses", [])
    if not wh_list:
        raise RuntimeError(
            "No se encontró ningún SQL Warehouse. "
            "Define DATABRICKS_WAREHOUSE_ID en las variables de entorno."
        )
    _warehouse_id_cache = wh_list[0]["id"]
    logger.info("Usando SQL Warehouse: %s (%s)", wh_list[0]["name"], _warehouse_id_cache)
    return _warehouse_id_cache
