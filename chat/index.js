import { AzureOpenAI } from "openai";

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

    // Cliente Azure OpenAI CORRECTO
    const client = new AzureOpenAI({
      apiKey: process.env.AZURE_OPENAI_API_KEY,
      apiVersion: process.env.AZURE_OPENAI_API_VERSION,
      endpoint: process.env.AZURE_OPENAI_ENDPOINT
    });

    const response = await client.chat.completions.create({
      model: process.env.AZURE_OPENAI_DEPLOYMENT,
      messages: [
        {
          role: "system",
          content: `Eres un analista experto en televisores.
Pide primero:
- Pulgadas
- Presupuesto
- Tipo (LED, QLED, OLED, etc.)
Si ya están dados, recomienda el mejor modelo.`
        },
        {
          role: "user",
          content: message
        }
      ],
      temperature: 0.4
    });

    context.res = {
      status: 200,
      body: {
        answer: response.choices[0].message.content
      }
    };

  } catch (err) {
    context.log("ERROR OPENAI:", err);
    context.res = {
      status: 500,
      body: { error: err.message }
    };
  }
}
