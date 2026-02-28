const sql = require("mssql");

/* ========= VALIDACIÓN ========= */

function hasEnoughInfo(message) {
  const hasInches = /\d+\s?(pulgadas|")/i.test(message);
  const hasBudget = /\d{3,}/.test(message);
  const hasFamily = /(led|qled|oled|nanocell|4k|full\s?hd|hd)/i.test(message);
  return hasInches && hasBudget && hasFamily;
}

function extractData(message) {
  const inches = message.match(/(\d+)\s?(pulgadas|")/i)?.[1];
  const budget = message.match(/(\d{3,})/)?.[1];
  const family = message.match(/(led|qled|oled|nanocell|4k|full\s?hd|hd)/i)?.[1]?.toLowerCase();

  return {
    inches: inches ? parseInt(inches) : null,
    budget: budget ? parseInt(budget) : null,
    family
  };
}

function cleanPrice(price) {
  if (!price) return null;
  return parseFloat(price.toString().replace(/[^\d.]/g, ""));
}

/* ========= AZURE FUNCTION ========= */

module.exports = async function (context, req) {

  let pool;

  try {

    context.log("🔵 Function iniciada");

    const message = req.body?.message;

    context.log("📩 Mensaje recibido:", message);

    if (!message || !hasEnoughInfo(message)) {
      context.res = {
        status: 200,
        headers: { "Content-Type": "application/json" },
        body: {
          info:
            "Hola 👋 soy su BOT asistente, indícame:\n- Pulgadas\n- Presupuesto\n- Familia (LED / QLED / OLED / NanoCell)\n\nEjemplo:\n👉 55 pulgadas QLED hasta 3000 soles"
        }
      };
      return;
    }

    const { inches, budget, family } = extractData(message);

    context.log("🔍 Datos extraídos:", { inches, budget, family });

    /* ========= CONEXIÓN SQL CORREGIDA ========= */

    pool = await sql.connect({
      server: process.env.SQL_SERVER,
      database: process.env.SQL_DATABASE,
      user: process.env.SQL_USER,
      password: process.env.SQL_PASSWORD,
      port: 1433,
      options: {
        encrypt: true,
        trustServerCertificate: true,
        enableArithAbort: true
      }
    });

    context.log("✅ Conexión SQL exitosa");

    const request = pool.request();
    request.input("family", sql.NVarChar, `%${family}%`);

    const result = await request.query(`
      SELECT TOP 200
        name,
        family,
        seller,
        url_product,
        url_image,
        internet_price,
        event_price,
        normal_price,
        cmr_price
      FROM dbo.hd_televisores
      WHERE LOWER(family) LIKE @family
    `);

    context.log("📊 Registros encontrados:", result.recordset.length);

    if (!result.recordset.length) {
      context.res = {
        status: 200,
        headers: { "Content-Type": "application/json" },
        body: { info: "No hay televisores disponibles en esa familia." }
      };
      return;
    }

    const televisores = result.recordset.map(tv => {

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

      const inchesMatch = tv.name.match(/(\d{2,3})/);
      const tvInches = inchesMatch ? parseInt(inchesMatch[1]) : null;

      return {
        name: tv.name,
        familia: tv.family,
        pulgadas: tvInches,
        vendedor: tv.seller,
        precio: bestPrice,
        url: tv.url_product,
        imagen: tv.url_image
      };

    }).filter(Boolean);

    const dentroPresupuesto = televisores.filter(tv => tv.precio <= budget);

    if (!dentroPresupuesto.length) {
      context.res = {
        status: 200,
        headers: { "Content-Type": "application/json" },
        body: {
          info: `❌ No contamos con televisores de ${inches}" ${family.toUpperCase()} dentro del presupuesto de ${budget} soles.`
        }
      };
      return;
    }

    dentroPresupuesto.sort((a, b) =>
      Math.abs(budget - a.precio) - Math.abs(budget - b.precio)
    );

    const mejor = dentroPresupuesto[0];

    context.res = {
      status: 200,
      headers: { "Content-Type": "application/json" },
      body: {
        message: `📌 Se recomienda este televisor porque se ajusta al presupuesto solicitado (${budget} soles).`,
        product: mejor
      }
    };

  } catch (error) {

    context.log("❌ ERROR COMPLETO:", error);
    context.log("STACK:", error.stack);

    context.res = {
      status: 500,
      headers: { "Content-Type": "application/json" },
      body: {
        error: error.message
      }
    };

  } finally {

    if (pool) {
      await pool.close();
      context.log("🔒 Conexión SQL cerrada");
    }

  }
};
