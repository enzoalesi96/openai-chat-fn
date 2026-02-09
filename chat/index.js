const sql = require("mssql");
const OpenAI = require("openai");

const openai = new OpenAI({
  apiKey: process.env.AZURE_OPENAI_API_KEY
});

function hasEnoughInfo(message) {
  const hasInches = /\d+\s?(pulgadas|")/i.test(message);
  const hasBudget = /\d{3,}/.test(message);
  const hasType = /(led|qled|oled|nanocell)/i.test(message);
  return hasInches && hasBudget && hasType;
}

async function queryDatabase() {
  const pool = await sql.connect({
    user: process.env.SQL_USER,
    password: process.env.SQL_PASSWORD,
    server: process.env.SQL_SERVER,
    database: process.env.SQL_DATABASE,
    options: { encrypt: true }
  });

  const result = await pool.request().query(`
    SELECT TOP 10
      nombre,
      marca,
      pulgadas,
      tecnologia,
      precio,
      descripcion
    FROM hd_televisores
  `);

  return result.recordset;
}

module.exports = async function (context, req) {
  try {
    const message = req.body?.message;

    if (!message) {
      context.res = { status: 400, body: { answer: "Envía un mensaje." } };
      return;
    }

    if (!hasEnoughInfo(message)) {
      context.res = {
        status: 200,
        body: {
          answer: `Necesito:
1️⃣ Pulgadas
2️⃣ Presupuesto
3️⃣ Tipo (LED, QLED, OLED, NanoCell)

Ejemplo:
👉 "55 pulgadas QLED hasta 3000 soles"`
        }
      };
      return;
    }

    const tvs = await queryDatabase();

    const contextFromDB = tvs.map(tv => `
Modelo: ${tv.nombre}
Marca: ${tv.marca}
Pulgadas: ${tv.pulgadas}
Tecnología: ${tv.tecnologia}
Precio: ${tv.precio}
Descripción: ${tv.descripcion}
`).join("\n");

    const prompt = `
Eres un analista experto en televisores.

Recomienda la mejor opción según lo que pide el usuario.
Si no hay coincidencias, indícalo claramente.

Información disponible:
${contextFromDB}
`;

    const completion = await openai.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [
        { role: "system", content: prompt },
        { role: "user", content: message }
      ],
      temperature: 0.4
    });

    context.res = {
      status: 200,
      body: { answer: completion.choices[0].message.content }
    };

  } catch (err) {
    context.log(err);
    context.res = { status: 500, body: { answer: "Error interno." } };
  }
};

