"""
function_app.py
---------------
VisionMatch AI — Azure Function (Python v2 programming model)
Arquitectura:
  - Azure OpenAI       → gpt-4o-mini
  - Databricks Serving → visionmatch-kmeans endpoint  (predict_cluster)
  - Databricks SQL     → data_oh_completa (Delta Lake) (fetch_top5)
  - Frontend           → https://salmon-pond-029452710.7.azurestaticapps.net
"""

import json
import logging
import azure.functions as func

from shared.helpers import has_enough_info, extract_params, CORS_HEADERS
from shared.databricks_client import predict_cluster, fetch_top5
from shared.aoai_client import generate_recommendation

app    = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preflight CORS
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["OPTIONS"])
def chat_preflight(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(status_code=204, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

@app.route(route="chat", methods=["GET", "POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    Flujo simplificado:
      1. Leer y validar mensaje del usuario.
      2. Extraer parámetros (pulgadas, presupuesto, familia).
      3. Llamar al Model Serving endpoint → cluster_id.
      4. Consultar Delta Lake directamente → top-5 candidatos.
      5. Enviar top-5 a gpt-4o-mini → respuesta conversacional.
    """

    logger.info("🔵 VisionMatch AI iniciada")

    # ── 1. Leer mensaje ────────────────────────────────────────────────────
    try:
        body    = req.get_json()
        message = body.get("message", "").strip()
    except Exception:
        message = req.params.get("message", "").strip()

    logger.info("📩 Mensaje: %s", message)

    # ── 2. Validar info mínima ─────────────────────────────────────────────
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

    # ── 3. Extraer parámetros ──────────────────────────────────────────────
    params  = extract_params(message)
    inches  = params["inches"]
    budget  = params["budget"]
    family  = params["family"]

    logger.info("🔍 Extraído → pulgadas=%s, presupuesto=%s, familia=%s",
                inches, budget, family)

    # ── 4. Predecir cluster vía Model Serving endpoint ─────────────────────
    try:
        cluster_id = predict_cluster(
            pulgadas=inches,
            presupuesto=budget,
            familia=family,
        )
    except Exception as exc:
        logger.error("❌ Error en Model Serving: %s", exc)
        return _json_response(
            {"error": f"Error al invocar el modelo de recomendación: {exc}"},
            status=500,
        )

    # ── 5. Obtener top-5 desde Delta Lake ──────────────────────────────────
    try:
        products = fetch_top5(
            cluster_id=cluster_id,
            pulgadas_ref=inches,
            presupuesto=budget,
            familia=family,
        )
        logger.info("📊 Productos obtenidos: %d", len(products))
    except Exception as exc:
        logger.error("❌ Error consultando Delta Lake: %s", exc)
        return _json_response(
            {"error": f"Error al consultar el catálogo: {exc}"},
            status=500,
        )

    if not products:
        return _json_response({
            "info": (
                f"❌ No encontramos televisores {family.upper()} de {inches}\" "
                f"dentro del presupuesto de S/ {budget:.0f}. "
                f"¿Deseas ampliar el presupuesto o cambiar la tecnología?"
            )
        })

    # ── 6. Generar respuesta con gpt-4o-mini ───────────────────────────────
    try:
        ai_response = generate_recommendation(
            user_message=message,
            inches=inches,
            budget=budget,
            family=family,
            products=products,
        )
        logger.info("🤖 Respuesta IA generada")
    except Exception as exc:
        logger.error("❌ Error Azure OpenAI: %s", exc)
        top = products[0]
        ai_response = (
            f"📌 Recomendación: **{top['name']}**\n"
            f"   • Pulgadas: {top['pulgadas']}\"  |  Familia: {top['familia']}\n"
            f"   • Precio: S/ {top['precio']}  |  Vendedor: {top['vendedor']}\n"
            f"   • Ver producto: {top['url']}"
        )

    # ── 7. Respuesta ───────────────────────────────────────────────────────
    return _json_response({
        "message":  ai_response,
        "products": products,
    })


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
