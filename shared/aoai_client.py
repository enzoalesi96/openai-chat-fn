"""
shared/aoai_client.py
----------------------
Cliente para Azure OpenAI con el modelo gpt-4o-mini.
Genera la respuesta conversacional final a partir del top-5 de Databricks.
"""

import os
import json
import logging
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cliente (singleton ligero — las Functions reusan el contenedor)
# ---------------------------------------------------------------------------

_client: AzureOpenAI | None = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=os.environ["AOAI_ENDPOINT"],
            api_key=os.environ["AOAI_API_KEY"],
            api_version="2024-12-01-preview",
        )
    return _client


DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o-mini")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
Eres VisionMatch AI, un asistente experto en televisores para el mercado peruano.
Tu tarea es ayudar al cliente a elegir el mejor televisor según su presupuesto,
las pulgadas deseadas y la tecnología de pantalla (LED, QLED, OLED, NanoCell).

Recibirás un JSON con hasta 5 televisores preseleccionados por un modelo KMeans
entrenado sobre el catálogo real. Debes:

1. Presentar brevemente el televisor mejor posicionado (rank 1) como tu
   recomendación principal, explicando por qué se ajusta al pedido.
2. Mencionar las otras opciones de forma concisa como alternativas.
3. Incluir el precio en soles (S/) y el vendedor para cada opción.
4. Si hay URLs de producto, invita al usuario a hacer clic para ver más detalles.
5. Mantén un tono amigable, experto y en español peruano.
6. No inventar datos — usa solo la información del JSON proporcionado.
""".strip()


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def generate_recommendation(
    user_message: str,
    inches: int,
    budget: float,
    family: str,
    products: list[dict],
) -> str:
    """
    Llama a gpt-4o-mini con el contexto del usuario y los productos
    seleccionados por KMeans. Devuelve la respuesta en texto plano.
    """
    client = _get_client()

    products_json = json.dumps(products, ensure_ascii=False, indent=2)

    user_content = (
        f"El cliente busca: {user_message}\n\n"
        f"Parámetros extraídos → pulgadas: {inches}, "
        f"presupuesto: S/ {budget:.0f}, familia: {family}\n\n"
        f"Top 5 televisores seleccionados por el modelo KMeans:\n"
        f"```json\n{products_json}\n```\n\n"
        f"Genera la recomendación al cliente."
    )

    logger.info("Llamando a Azure OpenAI deployment='%s'", DEPLOYMENT)

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        temperature=0.4,
        max_tokens=700,
    )

    return response.choices[0].message.content.strip()
