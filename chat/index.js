import { AzureOpenAI } from "openai";
import sql from "mssql";

/* ========= VALIDACIÓN ========= */
function hasEnoughInfo(message) {
  const hasInches = /\d+\s?(pulgadas|")/i.test(message);
  const hasBudget = /\d{3,}/.test(message);
  const hasFamily = /(led|qled|oled|nanocell)/i.test(message);
  return hasInches && hasBudget && hasFamily;
}

function extractData(message) {
  const inches = message.match(/(\d+)\s?(pulgadas|")/i)?.[1];
  const budget = message.match(/(\d{3,})/)?.[1];
  const family = message.match(/(led|qled|oled|nanocell)/i)?.[1]?.toLowerCase();

  return {
    inches: inches ? parseInt(inches) : null,
    budget: budget ? parseInt(budget) : null,
    family
  };
}

function cleanPrice(price) {
  if (!price) return null;
  return parseFloat(price.replace(/[^\d.]/g, ""));
}

/* ========= AZURE FUNCTION ========= */
export default async function (context, req) {
  try {
    const message = req.body?.message;

    if (!message) {
      context.res = {
        status: 200,
        body: {
          answer:
            "Buenas soy su BOT asistente, indícame:\n- Pulgadas\n- Presupuesto\n- Familia (LED / QLED / OLED / NanoCell)\n\nEjemplo:\n👉 55 pulgadas QLED hasta 3000 soles"
        }
      };
      return;
    }

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

    const { inches, budget, family } = extractData(message);

    /* ========= CONEXIÓN SQL ========= */
    const pool = await sql.connect({
      server: process.env.SQL_SERVER,
      database: process.env.SQL_DATABASE,
      user: process.env.SQL_USER,
      password: process.env.SQL_PASSWORD,
      options: { encrypt: true }
    });

    const request = pool.request();
    request.input("family", sql.NVarChar, `%${family}%`);

    const result = await request.query(`
      SELECT TOP 100
        name,
        family,
        seller,
        url_product,
        internet_price,
        event_price,
        normal_price,
        cmr_price
      FROM dbo.hd_televisores
      WHERE LOWER(family) LIKE @family
    `);

    if (!result.recordset.length) {
      context.res = {
        status: 200,
        body: { answer: "No hay televisores disponibles en esa familia." }
      };
      return;
    }

    /* ========= PROCESAR Y CALCULAR SCORE ========= */
    const televisores = result.recordset
      .map(tv => {

        const prices = [
          tv.internet_price,
          tv.event_price,
          tv.normal_price,
          tv.cmr_price
        ]
          .map(cleanPrice)
          .filter(p => p);

        if (!prices.length) return null;

        const bestPrice = Math.min(...prices);

        // extraer pulgadas desde el nombre
        const inchesMatch = tv.name.match(/(\d{2,3})/);
        const tvInches = inchesMatch ? parseInt(inchesMatch[1]) : inches;

        const priceDiff = Math.abs(bestPrice - budget);
        const inchDiff = Math.abs(tvInches - inches);

        const score = priceDiff + (inchDiff * 500);

        return {
          Name: tv.name,
          Familia: tv.family,
          Pulgadas: tvInches,
          Vendedor: tv.seller,
          Precio: bestPrice,
          URL_PRODUCTO: tv.url_product,
          score
        };
      })
      .filter(tv => tv !== null);

    if (!televisores.length) {
      context.res = {
        status: 200,
        body: { answer: "No encontré televisores disponibles." }
      };
      return;
    }

    televisores.sort((a, b) => a.score - b.score);

    const mejores = televisores.slice(0, 5);

    const contextoBD = mejores.map(tv => `
Name: ${tv.Name}
Familia: ${tv.Familia}
Resolucion: 4K
Pulgadas: ${tv.Pulgadas}
Vendedor: ${tv.Vendedor}
Precio: ${tv.Precio}
URL_PRODUCTO: ${tv.URL_PRODUCTO}
`).join("\n");

    /* ========= OPENAI ========= */
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
        { role: "user", content: message }
      ]
    });

    context.res = {
      status: 200,
      body: { answer: completion.choices[0].message.content }
    };

  } catch (error) {
    context.log("ERROR:", error);
    context.res = {
      status: 500,
      body: { error: error.message }
    };
  }
}
