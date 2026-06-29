"""
function_app.py  —  VisionMatch AI (modo diagnóstico paso a paso)
Cada paso devuelve su resultado inmediatamente para identificar dónde falla.
"""

import json
import logging
import os
import azure.functions as func

from shared.helpers import has_enough_info, extract_params, CORS_HEADERS

app    = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preflight CORS
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["OPTIONS"])
def chat_preflight(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(status_code=204, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# PASO 1 — Ping: verifica que la función responde
# GET /api/diagnostico/ping
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("PING recibido")
    return _json_response({"paso": 1, "ok": True, "mensaje": "Función activa y respondiendo"})


# ---------------------------------------------------------------------------
# PASO 2 — Variables de entorno: verifica que estén cargadas
# GET /api/diagnostico/env
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/env", methods=["GET"])
def check_env(req: func.HttpRequest) -> func.HttpResponse:
    vars_requeridas = [
        "AOAI_ENDPOINT",
        "AOAI_API_KEY",
        "AOAI_DEPLOYMENT",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
    ]
    resultado = {}
    for v in vars_requeridas:
        val = os.environ.get(v, "")
        if not val:
            resultado[v] = "❌ FALTANTE"
        elif v in ("AOAI_API_KEY", "DATABRICKS_TOKEN"):
            resultado[v] = f"✅ Presente ({val[:6]}...{val[-4:]})"
        else:
            resultado[v] = f"✅ {val}"

    todas_ok = all("✅" in v for v in resultado.values())
    return _json_response({
        "paso": 2,
        "ok": todas_ok,
        "variables": resultado,
    })


# ---------------------------------------------------------------------------
# PASO 3 — Model Serving: llama al endpoint KMeans
# GET /api/diagnostico/kmeans
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/kmeans", methods=["GET"])
def check_kmeans(req: func.HttpRequest) -> func.HttpResponse:
    import requests as req_lib
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if not token:
        return _json_response({"paso": 3, "ok": False, "error": "DATABRICKS_TOKEN no configurado"})

    url = (
        "https://adb-7405610080077220.0.azuredatabricks.net"
        "/serving-endpoints/visionmatch-kmeans/invocations"
    )
    payload = {
        "dataframe_records": [
            {"pulgadas": 55.0, "presupuesto": 3000.0, "familia_num": 1.0}
        ]
    }
    try:
        resp = req_lib.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        return _json_response({
            "paso": 3,
            "ok": True,
            "cluster_id": data.get("predictions", [None])[0],
            "raw": data,
        })
    except Exception as exc:
        return _json_response({
            "paso": 3,
            "ok": False,
            "error": str(exc),
            "status_code": getattr(exc, "response", None) and exc.response.status_code,
        })


# ---------------------------------------------------------------------------
# PASO 4 — SQL Warehouse: ejecuta una query simple en Delta Lake
# GET /api/diagnostico/sql
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/sql", methods=["GET"])
def check_sql(req: func.HttpRequest) -> func.HttpResponse:
    import requests as req_lib, time
    token        = os.environ.get("DATABRICKS_TOKEN", "")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    host         = "https://adb-7405610080077220.0.azuredatabricks.net"

    if not token:
        return _json_response({"paso": 4, "ok": False, "error": "DATABRICKS_TOKEN no configurado"})
    if not warehouse_id:
        return _json_response({"paso": 4, "ok": False, "error": "DATABRICKS_WAREHOUSE_ID no configurado"})

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        stmt = req_lib.post(
            f"{host}/api/2.0/sql/statements",
            headers=headers,
            json={
                "statement":       "SELECT COUNT(*) AS total FROM adbvisualmatch_7405610080077220.schema_tv.data_oh_completa",
                "warehouse_id":    warehouse_id,
                "wait_timeout":    "30s",
                "on_wait_timeout": "CONTINUE",
            },
            timeout=35,
        )
        stmt.raise_for_status()
        data  = stmt.json()
        state = data["status"]["state"]

        # Polling si quedó PENDING
        sid = data["statement_id"]
        for _ in range(10):
            if state in ("SUCCEEDED", "FAILED", "CANCELED"):
                break
            time.sleep(3)
            r     = req_lib.get(f"{host}/api/2.0/sql/statements/{sid}", headers=headers, timeout=15)
            data  = r.json()
            state = data["status"]["state"]

        if state != "SUCCEEDED":
            return _json_response({"paso": 4, "ok": False, "error": f"SQL terminó con estado: {state}", "detalle": data})

        total = data["result"]["data_array"][0][0]
        return _json_response({"paso": 4, "ok": True, "total_registros": total})

    except Exception as exc:
        return _json_response({"paso": 4, "ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# PASO 5 — Azure OpenAI: llama a gpt-4o-mini con un mensaje simple
# GET /api/diagnostico/openai
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/openai", methods=["GET"])
def check_openai(req: func.HttpRequest) -> func.HttpResponse:
    endpoint   = os.environ.get("AOAI_ENDPOINT", "")
    api_key    = os.environ.get("AOAI_API_KEY", "")
    deployment = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o-mini")

    if not endpoint or not api_key:
        return _json_response({"paso": 5, "ok": False, "error": "AOAI_ENDPOINT o AOAI_API_KEY no configurados"})

    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-12-01-preview",
        )
        response = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "Di solo: OK"}],
            max_tokens=10,
        )
        respuesta = response.choices[0].message.content.strip()
        return _json_response({"paso": 5, "ok": True, "respuesta": respuesta, "deployment": deployment})
    except Exception as exc:
        return _json_response({"paso": 5, "ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Chat endpoint principal (sin cambios)
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["GET", "POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("🔵 VisionMatch AI iniciada")

    try:
        body    = req.get_json()
        message = body.get("message", "").strip()
    except Exception:
        message = req.params.get("message", "").strip()

    logger.info("📩 Mensaje: %s", message)

    if not message or not has_enough_info(message):
        return _json_response({
            "info": (
                "Hola 👋 soy VisionMatch AI, tu asistente de televisores.\n"
                "Para ayudarte necesito saber:\n"
                "  • Pulgadas deseadas\n"
                "  • Presupuesto en soles\n"
                "  • Tecnología (LED / QLED / OLED / NanoCell)\n\n"
                "Ejemplo: 👉 55 pulgadas QLED hasta 3000 soles"
            )
        })

    params = extract_params(message)
    inches = params["inches"]
    budget = params["budget"]
    family = params["family"]
    logger.info("🔍 Extraído → pulgadas=%s, presupuesto=%s, familia=%s", inches, budget, family)

    from shared.databricks_client import predict_cluster, fetch_top5
    from shared.aoai_client import generate_recommendation

    try:
        cluster_id = predict_cluster(pulgadas=inches, presupuesto=budget, familia=family)
    except Exception as exc:
        logger.error("❌ Error Model Serving: %s", exc)
        return _json_response({"error": f"Error modelo KMeans: {exc}"}, status=500)

    try:
        products = fetch_top5(cluster_id=cluster_id, pulgadas_ref=inches, presupuesto=budget, familia=family)
        logger.info("📊 Productos: %d", len(products))
    except Exception as exc:
        logger.error("❌ Error Delta Lake: %s", exc)
        return _json_response({"error": f"Error catálogo: {exc}"}, status=500)

    if not products:
        return _json_response({
            "info": (
                f"❌ No encontramos televisores {family.upper()} de {inches}\" "
                f"dentro del presupuesto de S/ {budget:.0f}. "
                f"¿Deseas ampliar el presupuesto o cambiar la tecnología?"
            )
        })

    try:
        ai_response = generate_recommendation(
            user_message=message, inches=inches, budget=budget,
            family=family, products=products,
        )
    except Exception as exc:
        logger.error("❌ Error OpenAI: %s", exc)
        top = products[0]
        ai_response = (
            f"📌 Recomendación: **{top['name']}**\n"
            f"   • {top['pulgadas']}\" | {top['familia']} | S/ {top['precio']}\n"
            f"   • Vendedor: {top['vendedor']}\n"
            f"   • Ver: {top['url']}"
        )

    return _json_response({"message": ai_response, "products": products})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _json_response(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(data, ensure_ascii=False),
        status_code=status,
        headers=CORS_HEADERS,
        mimetype="application/json",
    )
