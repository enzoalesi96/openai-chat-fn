"""
shared/databricks_client.py
----------------------------
Interacciones con Databricks:
  1. Invocar el Model Serving endpoint (visionmatch-kmeans) para obtener el prediction.
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
SOURCE_TABLE = "adbvisualmatch_7405610080077220.schema_tv.data_televisores"

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
# 1. Invocar el Model Serving endpoint → prediction
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

    Adaptado al NUEVO formato de datos (data_televisores):
      - Precios YA son numéricos (DOUBLE), no strings → sin REGEXP_REPLACE.
      - Campos de precio: discount_price, event_price, internet_price, normal_price
        (ya NO existe cmr_price).
      - Pulgadas en formatos variados en el nombre: 50", 32'', 43”, "65 pulgadas",
        "55 Pulg" → regex flexible.
      - Se incluye la TIENDA (seller) en los resultados.

    Estrategia:
      1. Filtra por el cluster del usuario (familia_num == cluster_id).
      2. Si hay < 5 resultados, amplía a todo el catálogo dentro del presupuesto.
    """
    presupuesto_max = presupuesto * 1.10

    # CTE base: calcula precio mínimo, pulgadas y familia_num
    # Nota: los precios ya son numéricos; solo tomamos el mínimo válido (>0).
    # El regex de pulgadas busca un número 2-3 dígitos seguido de un indicador
    # de pulgadas ("|''|”|pulg) y, si no, cae a un número suelto en rango de TV.
    base_cte = f"""
    WITH base AS (
      SELECT
        name,
        family,
        seller,
        url_product,
        url_image,
        brand,
        -- TIENDA: se deriva del dominio de la URL del producto
        CASE
          WHEN LOWER(url_product) LIKE '%oechsle%'  THEN 'Oechsle'
          WHEN LOWER(url_product) LIKE '%plazavea%' THEN 'Plaza Vea'
          WHEN LOWER(url_product) LIKE '%tottus%'   THEN 'Tottus'
          ELSE 'Otra tienda'
        END AS tienda,
        -- Precio mínimo válido entre los 4 campos numéricos
        LEAST(
          COALESCE(NULLIF(CAST(discount_price AS DOUBLE), 0), 999999),
          COALESCE(NULLIF(CAST(event_price    AS DOUBLE), 0), 999999),
          COALESCE(NULLIF(CAST(internet_price AS DOUBLE), 0), 999999),
          COALESCE(NULLIF(CAST(normal_price   AS DOUBLE), 0), 999999)
        ) AS precio,
        -- Pulgadas: primero patrón "NN<indicador>", luego número suelto en rango
        COALESCE(
          TRY_CAST(REGEXP_EXTRACT(name, '([0-9]{{2,3}}) *(?:"|\'\'|”|[Pp]ulg)', 1) AS INT),
          TRY_CAST(REGEXP_EXTRACT(name, '\\b([2-9][0-9]|1[0-9]{{2}})\\b', 1) AS INT)
        ) AS pulgadas,
        -- Encoding de familia (igual que el modelo KMeans)
        CASE
          WHEN UPPER(family) LIKE '%OLED%' AND UPPER(family) NOT LIKE '%QLED%' THEN 2.0
          WHEN UPPER(family) LIKE '%QLED%' OR UPPER(family) LIKE '%QNED%'       THEN 1.0
          WHEN UPPER(family) LIKE '%NANO%'                                       THEN 3.0
          ELSE 0.0
        END AS familia_num
      FROM {SOURCE_TABLE}
    ),
    con_precio AS (
      SELECT *
      FROM base
      WHERE precio < 999999
        AND precio > 0
        AND pulgadas IS NOT NULL
        AND pulgadas BETWEEN 20 AND 110
    )
    """

    select_tail = f"""
    SELECT
      name,
      family AS familia,
      pulgadas,
      ROUND(precio, 2) AS precio,
      tienda,
      seller AS vendedor,
      brand AS marca,
      url_product AS url,
      url_image AS imagen,
      ROUND((precio / {presupuesto}) * 100 - ABS(pulgadas - {pulgadas_ref}) * 2.0, 2) AS score
    FROM con_precio
    WHERE precio <= {presupuesto_max}
    """

    # 1) Con filtro de cluster
    sql_cluster = (
        base_cte + select_tail
        + f" AND familia_num = {float(cluster_id)} ORDER BY score DESC LIMIT 5"
    )
    rows = _run_sql(sql_cluster)

    # 2) Fallback: todo el catálogo dentro del presupuesto
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