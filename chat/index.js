import { AzureOpenAI } from "openai";

/* ========= VALIDACIÓN ========= */
function hasEnoughInfo(message) {
  const hasInches = /\d+\s?(pulgadas|")/i.test(message);
  const hasBudget = /\d{3,}/.test(message);
  const hasFamily = /(led|qled|oled|nanocell)/i.test(message);
  return hasInches && hasBudget && hasFamily;
}

/* ========= DATA SIMULADA (BD) ========= */
const televisores = [
  {
    nombre: "Samsung QLED Q80A",
    familia: "QLED",
    resolucion: "4K",
    pulgadas: 55,
    precio: 2999,
    seller: "Samsung Perú",
    url_producto:
      "https://www.samsung.com/pe/tvs/qled-tv/q80a-55-inch-qn55q80aagxpe/"
  }
];

/* ========= AZURE FUNCTION ========= */
export default async function (context, req) {
  try {
    const message = req.body?.message;

    if (!message) {
      context.res = {
        status: 400,
        body: { error: "Falta el campo message" }
      };
      return;
    }

    /* ---- SI FALTAN DATOS ---- */
    if (!hasEnoughInfo(message)) {
      context.res = {
        status: 200,
        body: {
          answer:
            "Buenas soy su BOT asistente, indícame:\n- Pulgadas\n- Presupuesto\n- Familia (LED / QLED / OLED / NanoCell)\n\nEjemplo:\n👉 55 pulgadas QLED hasta 3000 soles"
        }
      };
      return;
    }

    /* ---- CONTEXTO BD ---- */
    const contextoBD = televisores.map(tv => `
Name: ${tv.nombre}
Familia: ${tv.familia}
Resolucion: ${tv.resolucion}
Pulgadas: ${tv.pulgadas}
Precio: ${tv.precio}
Vendedor: ${tv.seller}
URL_PRODUCTO: ${tv.url_producto}
`).join("\n");

    /* ---- OPENAI ---- */
    const client = new AzureOpenAI({
      apiKey: process.env.AZURE_OPENAI_API_KEY,
      apiVersion: process.env.AZURE_OPENAI_API_VERSION,
      endpoint: process.env.AZURE_OPENAI_ENDPOINT
    });

    const completion = await client.chat.completions.create({
      model: process.env.AZURE_OPENAI_DEPLOYMENT,
      temperature: 0.2,
      messages: [
        {
          role: "system",
          content: `
Eres un analista experto en televisores.

Selecciona SOLO UN televisor de la base de datos.

Devuelve ÚNICAMENTE este formato exacto:
En base a su requerimiento se le proporciona el siguiente producto:
- Name:
- Familia:
- Resolucion:
- Pulgadas:
- Vendedor:
- Precio:
- URL_PRODUCTO:

Base de datos:
${contextoBD}
`
        },
        {
          role: "user",
          content: message
        }
      ]
    });

    context.res = {
      status: 200,
      body: {
        answer: completion.choices[0].message.content
      }
    };

  } catch (error) {
    context.log("ERROR:", error);
    context.res = {
      status: 500,
      body: { error: "Error interno del servidor" }
    };
  }
}
