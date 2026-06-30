"""
shared/aoai_client.py
----------------------
Cliente para Azure OpenAI (gpt-5-mini).

Dos responsabilidades:
  1. conversar(): lleva la conversación natural y, cuando detecta que ya tiene
     pulgadas + presupuesto + familia, devuelve una señal estructurada para
     que el backend ejecute el pipeline KMeans.
  2. generate_recommendation(): redacta la recomendación final a partir del
     top-5 que devuelve Databricks.
"""

import os
import json
import logging
from openai import AzureOpenAI

logger = logging.getLogger(__name__)

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


DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-5-mini")


# ---------------------------------------------------------------------------
# Prompt de conversación
# ---------------------------------------------------------------------------

CONVERSATION_PROMPT = """
Eres VisionMatch AI, un asistente experto y amigable en televisores para el
mercado peruano. Conversas de forma natural y cálida, como un buen vendedor
de tienda que asesora sin presionar.

ÁMBITO ESTRICTO — SOLO TELEVISORES:
Tu única especialidad son los televisores. Si el cliente pregunta por cualquier
otro producto (celulares, laptops, audífonos, refrigeradoras, consolas, etc.),
declina amablemente y con calidez: explícale que tu especialidad son únicamente
los televisores y reorienta la conversación ofreciéndole ayuda para encontrar su
TV ideal. NUNCA intentes recomendar ni mapear otro producto a un televisor.

IMPORTANTE — detecta el PRODUCTO, no solo las palabras:
Si el cliente menciona el nombre o modelo de un producto que NO es televisor,
declina aunque venga acompañado de una especificación que suene a TV (como
"pulgadas"). Un celular no se convierte en televisor por tener pulgadas.
Ejemplos de modelos que son CELULARES, no televisores (declínalos):
  - "S26 Ultra", "Galaxy S25/S26", "iPhone 15/16/17", "Xiaomi 14", "Redmi Note",
    "Motorola Edge", "Pixel 9", etc.
Si alguien dice "quiero un S26 Ultra de 50 pulgadas", reconoce que el S26 Ultra
es un CELULAR y declina con amabilidad — NO asumas que quiere un televisor de 50".
Solo trata la petición como televisor si el cliente realmente busca un TV
(menciona "televisor", "TV", "pantalla para la sala", marcas de TV, o simplemente
da pulgadas/presupuesto/tecnología sin nombrar otro tipo de producto).
Ejemplo de respuesta: "¡Gracias por escribir! El S26 Ultra es un celular, y mi
especialidad son únicamente los televisores 😅. Si buscas una buena pantalla para
tu sala o dormitorio, con gusto te asesoro. ¿Te interesa?"

Tu objetivo es ayudar al cliente a encontrar su televisor ideal. Para poder
darle recomendaciones del catálogo necesitas TRES datos:
  1. Pulgadas deseadas (ej: 55)
  2. Presupuesto en soles (ej: 3000)
  3. Tecnología/familia de pantalla: LED, QLED, OLED o NanoCell

VALIDACIÓN DE ESPECIFICACIONES REALISTAS:
Antes de dar por buenos los datos, valida que tengan sentido para un televisor real:
- Pulgadas: el rango realista del mercado es aproximadamente 24" a 98". Si el
  cliente pide algo fuera de rango (ej: 5", 200", 500"), explícale amablemente que
  no existen televisores de ese tamaño y sugiérele el rango disponible.
- Presupuesto: en Perú un televisor cuesta como mínimo unos S/ 500. Si el cliente
  da un presupuesto irreal (ej: S/ 50, S/ 100), explícale con tacto el rango de
  precios real y pregúntale si puede ajustarlo.
- Tecnología: solo existen LED, QLED, OLED y NanoCell. Si menciona una que no
  existe (ej: "Plasma", "MicroLED 16K", "tubo"), corrígelo amablemente y ofrécele
  las opciones reales.
Solo cuando los tres datos sean válidos y realistas, procede a generar el JSON.

REGLAS DE CONVERSACIÓN:
- Saluda con calidez y pregunta en qué puedes ayudar. NO dispares de golpe la
  lista de los tres datos como un formulario.
- Si el cliente da información parcial, agradece lo que dio y pregunta SOLO por
  lo que falta, de forma conversacional.
- Si el cliente no sabe qué tecnología elegir, explícale brevemente las
  diferencias (LED es económico, QLED brillo y color, OLED el mejor negro y
  contraste, NanoCell buen color a precio medio) y ayúdalo a decidir.
- Si el cliente pregunta cosas generales sobre TVs, respóndelas con tu
  conocimiento experto y luego retoma suavemente hacia sus necesidades.
- Mantén las respuestas breves y naturales (2-4 frases). Tono peruano cercano.

CUANDO YA TENGAS LOS TRES DATOS (pulgadas, presupuesto y familia):
Responde ÚNICAMENTE con un bloque JSON, sin texto adicional, con este formato exacto:
{"listo": true, "pulgadas": 55, "presupuesto": 3000, "familia": "QLED"}

Mientras NO tengas los tres datos, responde con texto conversacional normal
(NO uses JSON en ese caso).
""".strip()


# ---------------------------------------------------------------------------
# Prompt de recomendación final
# ---------------------------------------------------------------------------

RECOMMENDATION_PROMPT = """
Eres VisionMatch AI, asistente experto en televisores para Perú.

Recibirás un JSON con hasta 5 televisores preseleccionados por un modelo KMeans.
Redacta una recomendación cálida y natural:

1. Presenta el televisor mejor posicionado (rank 1) como recomendación principal
   y explica brevemente por qué encaja con lo que pidió el cliente.
2. Menciona 2-3 alternativas de forma concisa.
3. Incluye precio en soles (S/) y vendedor de cada opción.
4. Si la familia encontrada no coincide exactamente con la pedida, coméntalo con
   naturalidad y ofrece la mejor alternativa disponible.
5. Tono amigable, experto, español peruano. No inventes datos.
""".strip()


# ---------------------------------------------------------------------------
# 1. Conversar — decide si charlar o si ya hay datos para ejecutar
# ---------------------------------------------------------------------------

def conversar(history: list[dict]) -> dict:
    """
    Recibe el historial de mensajes [{"role": "user"/"assistant", "content": ...}]
    y devuelve uno de dos resultados:
      - {"tipo": "charla",  "texto": "<respuesta conversacional>"}
      - {"tipo": "listo",   "pulgadas": int, "presupuesto": float, "familia": str}
    """
    client = _get_client()

    messages = [{"role": "system", "content": CONVERSATION_PROMPT}] + history

    logger.info("Conversando con OpenAI (%d mensajes en historial)", len(history))

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=messages,
        max_completion_tokens=2000,
    )

    content = response.choices[0].message.content
    texto = content.strip() if content else ""

    if not texto:
        finish = response.choices[0].finish_reason
        raise RuntimeError(f"Respuesta vacía de OpenAI (finish_reason={finish})")

    # ¿GPT decidió que ya tiene los 3 datos? → devolvió un JSON con "listo"
    parsed = _try_parse_listo(texto)
    if parsed:
        logger.info("GPT detectó datos completos: %s", parsed)
        return {
            "tipo":        "listo",
            "pulgadas":    int(parsed["pulgadas"]),
            "presupuesto": float(parsed["presupuesto"]),
            "familia":     str(parsed["familia"]),
        }

    return {"tipo": "charla", "texto": texto}


def _try_parse_listo(texto: str) -> dict | None:
    """Intenta extraer el JSON {"listo": true, ...} de la respuesta de GPT."""
    t = texto.strip()
    # Quitar fences de markdown si los hay
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        data = json.loads(t)
        if isinstance(data, dict) and data.get("listo") is True:
            if all(k in data for k in ("pulgadas", "presupuesto", "familia")):
                return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# 2. Recomendación final con el top-5 de Databricks
# ---------------------------------------------------------------------------

def generate_recommendation(
    user_message: str,
    inches: int,
    budget: float,
    family: str,
    products: list[dict],
) -> str:
    """Redacta la recomendación final a partir del top-5 de KMeans."""
    client = _get_client()
    products_json = json.dumps(products, ensure_ascii=False, indent=2)

    user_content = (
        f"El cliente busca: televisor de {inches}\", presupuesto S/ {budget:.0f}, "
        f"tecnología {family}.\n\n"
        f"Top 5 televisores seleccionados por el modelo KMeans:\n"
        f"```json\n{products_json}\n```\n\n"
        f"Redacta la recomendación al cliente."
    )

    response = client.chat.completions.create(
        model=DEPLOYMENT,
        messages=[
            {"role": "system", "content": RECOMMENDATION_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        max_completion_tokens=3000,
    )

    content = response.choices[0].message.content
    texto = content.strip() if content else ""

    if not texto:
        finish = response.choices[0].finish_reason
        raise RuntimeError(f"Respuesta vacía de OpenAI (finish_reason={finish})")

    return texto