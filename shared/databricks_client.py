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

    resp = requests.post(
        SERVING_ENDPOINT_URL,
        headers=_AUTH_HEADERS,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()

    data = resp.json()
    # El endpoint devuelve: {"predictions": [<cluster_id>]}
    cluster_id = int(data["predictions"][0])
    logger.info("✅ Cluster asignado: %d", cluster_id)
    return cluster_id


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
    Ejecuta una query SQL sobre la tabla Delta Lake para obtener el top-5
    de televisores del cluster del usuario dentro del presupuesto (+10 % margen).

    Si hay menos de 5 candidatos en el cluster, amplía a toda la familia.
    Devuelve una lista de dicts con los campos del producto.
    """
    presupuesto_max = presupuesto * 1.10
    familia_upper   = familia.upper()

    # La misma lógica de scoring que modelo_televisores.py:
    # score = (precio / presupuesto) * 100 − |pulgadas − ref| * 2
    sql = f"""
    WITH precios AS (
      SELECT
        name,
        family,
        seller,
        url_product,
        url_image,
        -- Precio mínimo válido entre los cuatro campos
        LEAST(
          CASE WHEN CAST(REGEXP_REPLACE(internet_price, '[^0-9.]', '') AS DOUBLE) > 0
               THEN CAST(REGEXP_REPLACE(internet_price, '[^0-9.]', '') AS DOUBLE) END,
          CASE WHEN CAST(REGEXP_REPLACE(event_price,    '[^0-9.]', '') AS DOUBLE) > 0
               THEN CAST(REGEXP_REPLACE(event_price,    '[^0-9.]', '') AS DOUBLE) END,
          CASE WHEN CAST(REGEXP_REPLACE(normal_price,   '[^0-9.]', '') AS DOUBLE) > 0
               THEN CAST(REGEXP_REPLACE(normal_price,   '[^0-9.]', '') AS DOUBLE) END,
          CASE WHEN CAST(REGEXP_REPLACE(cmr_price,      '[^0-9.]', '') AS DOUBLE) > 0
               THEN CAST(REGEXP_REPLACE(cmr_price,      '[^0-9.]', '') AS DOUBLE) END
        ) AS precio,
        CAST(REGEXP_EXTRACT(name, '\\\\b([2-9][0-9]|1[0-9]{{2}})\\\\b', 1) AS INT) AS pulgadas,
        CASE
          WHEN UPPER(family) LIKE '%OLED%'     THEN 2.0
          WHEN UPPER(family) LIKE '%QLED%'     THEN 1.0
          WHEN UPPER(family) LIKE '%NANOCELL%' THEN 3.0
          ELSE 0.0
        END AS familia_num
      FROM {SOURCE_TABLE}
      WHERE REGEXP_EXTRACT(name, '\\\\b([2-9][0-9]|1[0-9]{{2}})\\\\b', 1) <> ''
    ),
    cluster_scored AS (
      SELECT
        name, family AS familia, pulgadas, precio, seller AS vendedor,
        url_product AS url, url_image AS imagen,
        ROUND((precio / {presupuesto}) * 100 - ABS(pulgadas - {pulgadas_ref}) * 2.0, 4) AS score
      FROM precios
      WHERE precio IS NOT NULL
        AND precio > 0
        AND precio <= {presupuesto_max}
        AND pulgadas IS NOT NULL
        -- Filtrar por el cluster: replicamos la lógica familia_num = cluster mapping
        AND CASE
              WHEN UPPER(family) LIKE '%OLED%'     THEN 2.0
              WHEN UPPER(family) LIKE '%QLED%'     THEN 1.0
              WHEN UPPER(family) LIKE '%NANOCELL%' THEN 3.0
              ELSE 0.0
            END = {float(cluster_id)}
    )
    SELECT name, familia, pulgadas, ROUND(precio, 2) AS precio,
           vendedor, url, imagen, score
    FROM cluster_scored
    ORDER BY score DESC
    LIMIT 5
    """

    rows = _run_sql(sql)

    # Si el cluster tiene menos de 5 resultados → ampliar a la familia completa
    if len(rows) < 5:
        logger.warning(
            "⚠️  Solo %d candidatos en cluster %d. Ampliando a familia '%s'...",
            len(rows), cluster_id, familia,
        )
        sql_fallback = f"""
        WITH precios AS (
          SELECT
            name, family,
            seller, url_product, url_image,
            LEAST(
              CASE WHEN CAST(REGEXP_REPLACE(internet_price, '[^0-9.]', '') AS DOUBLE) > 0
                   THEN CAST(REGEXP_REPLACE(internet_price, '[^0-9.]', '') AS DOUBLE) END,
              CASE WHEN CAST(REGEXP_REPLACE(event_price,    '[^0-9.]', '') AS DOUBLE) > 0
                   THEN CAST(REGEXP_REPLACE(event_price,    '[^0-9.]', '') AS DOUBLE) END,
              CASE WHEN CAST(REGEXP_REPLACE(normal_price,   '[^0-9.]', '') AS DOUBLE) > 0
                   THEN CAST(REGEXP_REPLACE(normal_price,   '[^0-9.]', '') AS DOUBLE) END,
              CASE WHEN CAST(REGEXP_REPLACE(cmr_price,      '[^0-9.]', '') AS DOUBLE) > 0
                   THEN CAST(REGEXP_REPLACE(cmr_price,      '[^0-9.]', '') AS DOUBLE) END
            ) AS precio,
            CAST(REGEXP_EXTRACT(name, '\\\\b([2-9][0-9]|1[0-9]{{2}})\\\\b', 1) AS INT) AS pulgadas
          FROM {SOURCE_TABLE}
          WHERE UPPER(family) LIKE '%{familia_upper}%'
            AND REGEXP_EXTRACT(name, '\\\\b([2-9][0-9]|1[0-9]{{2}})\\\\b', 1) <> ''
        )
        SELECT
          name, family AS familia, pulgadas, ROUND(precio, 2) AS precio,
          seller AS vendedor, url_product AS url, url_image AS imagen,
          ROUND((precio / {presupuesto}) * 100 - ABS(pulgadas - {pulgadas_ref}) * 2.0, 4) AS score
        FROM precios
        WHERE precio IS NOT NULL AND precio > 0 AND precio <= {presupuesto_max}
          AND pulgadas IS NOT NULL
        ORDER BY score DESC
        LIMIT 5
        """
        rows = _run_sql(sql_fallback)

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
        raise RuntimeError(f"SQL statement terminó con estado '{state}'.")

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
