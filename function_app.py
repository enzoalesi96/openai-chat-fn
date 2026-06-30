"""
function_app.py  —  VisionMatch AI (modo diagnóstico paso a paso)
Imports de shared movidos DENTRO de cada función para evitar
que un error de importación tumbe toda la app.
"""

import json
import logging
import os
import azure.functions as func

app    = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
logger = logging.getLogger(__name__)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

def _json_response(data: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(data, ensure_ascii=False),
        status_code=status,
        headers=CORS_HEADERS,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# CORS preflight
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["OPTIONS"])
def chat_preflight(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(status_code=204, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# PASO 1 — Ping básico
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return _json_response({"paso": 1, "ok": True, "mensaje": "Función activa"})


# ---------------------------------------------------------------------------
# PASO 2 — Variables de entorno
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/env", methods=["GET"])
def check_env(req: func.HttpRequest) -> func.HttpResponse:
    vars_req = [
        "AOAI_ENDPOINT",
        "AOAI_API_KEY",
        "AOAI_DEPLOYMENT",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
    ]
    resultado = {}
    for v in vars_req:
        val = os.environ.get(v, "")
        if not val:
            resultado[v] = "FALTANTE"
        elif v in ("AOAI_API_KEY", "DATABRICKS_TOKEN"):
            resultado[v] = f"OK ({val[:6]}...{val[-4:]})"
        else:
            resultado[v] = f"OK: {val}"

    todas_ok = all("OK" in v for v in resultado.values())
    return _json_response({"paso": 2, "ok": todas_ok, "variables": resultado})


# ---------------------------------------------------------------------------
# PASO 3 — Model Serving KMeans
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/kmeans", methods=["GET"])
def check_kmeans(req: func.HttpRequest) -> func.HttpResponse:
    import requests as rq
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if not token:
        return _json_response({"paso": 3, "ok": False, "error": "DATABRICKS_TOKEN faltante"})

    url = (
        "https://adb-7405610080077220.0.azuredatabricks.net"
        "/serving-endpoints/visionmatch-kmeans/invocations"
    )
    try:
        r = rq.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"dataframe_records": [{"pulgadas": 55.0, "presupuesto": 3000.0, "familia_num": 1.0}]},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return _json_response({"paso": 3, "ok": True, "cluster_id": data.get("predictions", [None])[0], "raw": data})
    except Exception as exc:
        return _json_response({"paso": 3, "ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# PASO 4 — SQL Warehouse Delta Lake
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/sql", methods=["GET"])
def check_sql(req: func.HttpRequest) -> func.HttpResponse:
    import requests as rq, time
    token        = os.environ.get("DATABRICKS_TOKEN", "")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    host         = "https://adb-7405610080077220.0.azuredatabricks.net"

    if not token:
        return _json_response({"paso": 4, "ok": False, "error": "DATABRICKS_TOKEN faltante"})
    if not warehouse_id:
        return _json_response({"paso": 4, "ok": False, "error": "DATABRICKS_WAREHOUSE_ID faltante"})

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = rq.post(
            f"{host}/api/2.0/sql/statements",
            headers=headers,
            json={
                "statement": "SELECT COUNT(*) AS total FROM adbvisualmatch_7405610080077220.schema_tv.data_oh_completa",
                "warehouse_id": warehouse_id,
                "wait_timeout": "30s",
                "on_wait_timeout": "CONTINUE",
            },
            timeout=35,
        )
        r.raise_for_status()
        data  = r.json()
        state = data["status"]["state"]
        sid   = data["statement_id"]

        for _ in range(10):
            if state in ("SUCCEEDED", "FAILED", "CANCELED"):
                break
            time.sleep(3)
            data  = rq.get(f"{host}/api/2.0/sql/statements/{sid}", headers=headers, timeout=15).json()
            state = data["status"]["state"]

        if state != "SUCCEEDED":
            return _json_response({"paso": 4, "ok": False, "error": f"SQL estado: {state}", "detalle": data})

        total = data["result"]["data_array"][0][0]
        return _json_response({"paso": 4, "ok": True, "total_registros": total})
    except Exception as exc:
        return _json_response({"paso": 4, "ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# PASO 5 — Azure OpenAI
# ---------------------------------------------------------------------------

@app.route(route="diagnostico/openai", methods=["GET"])
def check_openai(req: func.HttpRequest) -> func.HttpResponse:
    endpoint   = os.environ.get("AOAI_ENDPOINT", "")
    api_key    = os.environ.get("AOAI_API_KEY", "")
    deployment = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o-mini")

    if not endpoint or not api_key:
        return _json_response({"paso": 5, "ok": False, "error": "AOAI_ENDPOINT o AOAI_API_KEY faltantes"})

    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version="2024-12-01-preview")
        r = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": "Di solo: OK"}],
            max_completion_tokens=10,
        )
        return _json_response({"paso": 5, "ok": True, "respuesta": r.choices[0].message.content.strip()})
    except Exception as exc:
        return _json_response({"paso": 5, "ok": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Chat principal — conversacional
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["GET", "POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    Endpoint conversacional.

    Espera un body JSON con el historial completo:
      { "history": [ {"role":"user","content":"hola"}, ... ] }

    También acepta el formato simple { "message": "..." } por compatibilidad.

    Flujo:
      1. GPT-5 conversa naturalmente con el usuario.
      2. Cuando GPT detecta que ya tiene pulgadas + presupuesto + familia,
         devuelve una señal; el backend ejecuta KMeans + Delta Lake.
      3. GPT redacta la recomendación final con el top-5.
    """
    logger.info("VisionMatch AI - chat conversacional")

    # ── Leer historial o mensaje simple ────────────────────────────────────
    history = []
    try:
        body = req.get_json()
        if isinstance(body.get("history"), list) and body["history"]:
            history = body["history"]
        elif body.get("message"):
            history = [{"role": "user", "content": body["message"].strip()}]
    except Exception:
        msg = req.params.get("message", "").strip()
        if msg:
            history = [{"role": "user", "content": msg}]

    if not history:
        return _json_response({
            "message": "Hola, soy VisionMatch AI. ¿En qué televisor estás pensando hoy?"
        })

    # ── Paso 1: conversar (GPT decide si charlar o si ya hay datos) ─────────
    from shared.aoai_client import conversar, generate_recommendation

    try:
        resultado = conversar(history)
    except Exception as exc:
        logger.error("Error en conversación: %s", exc)
        return _json_response(
            {"message": "Disculpa, tuve un problema procesando tu mensaje. ¿Puedes repetirlo?"},
            status=200,
        )

    # ── Si todavía falta info → devolver la charla y esperar más ───────────
    if resultado["tipo"] == "charla":
        return _json_response({"message": resultado["texto"]})

    # ── Si ya hay datos → ejecutar el pipeline ─────────────────────────────
    inches = resultado["pulgadas"]
    budget = resultado["presupuesto"]
    family = resultado["familia"]
    logger.info("Datos completos: %s\" / S/%s / %s", inches, budget, family)

    from shared.databricks_client import predict_cluster, fetch_top5
    try:
        cluster_id = predict_cluster(pulgadas=inches, presupuesto=budget, familia=family)
    except Exception as exc:
        logger.error("Error KMeans: %s", exc)
        return _json_response({"error": f"Error modelo KMeans: {exc}"}, status=500)

    try:
        products = fetch_top5(cluster_id=cluster_id, pulgadas_ref=inches, presupuesto=budget, familia=family)
    except Exception as exc:
        logger.error("Error Delta Lake: %s", exc)
        return _json_response({"error": f"Error catalogo: {exc}"}, status=500)

    if not products:
        return _json_response({
            "message": (
                f"Lo siento, no tengo actualmente ningún televisor que cumpla con "
                f"esas especificaciones: {family} de {inches}\" dentro de un "
                f"presupuesto de S/ {budget:.0f}. 😕\n\n"
                f"¿Te gustaría que ajustemos algo? Por ejemplo, ampliar un poco el "
                f"presupuesto, considerar otro tamaño, o probar con otra tecnología "
                f"de pantalla."
            )
        })

    try:
        ai_response = generate_recommendation(
            user_message="", inches=inches, budget=budget,
            family=family, products=products,
        )
    except Exception as exc:
        logger.error("Error OpenAI recomendacion: %s", exc)
        top = products[0]
        ai_response = (
            f"Te recomiendo el {top['name']}: {top['pulgadas']}\", "
            f"{top['familia']}, S/ {top['precio']} ({top['vendedor']})."
        )

    return _json_response({"message": ai_response, "products": products})