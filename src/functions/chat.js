import fetch from "node-fetch";

export default async function (context, req) {
  try {
    const message = req.body?.message;
    if (!message) {
      context.res = { status: 400, body: "Mensaje requerido" };
      return;
    }

    const payload = {
      messages: [
        {
          role: "system",
          content: "Eres un asistente experto en tecnología y televisores."
        },
        { role: "user", content: message }
      ],
      temperature: 0.7
    };

    const response = await fetch(
      `${process.env.AZURE_OPENAI_ENDPOINT}/openai/deployments/${process.env.AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version=2024-02-15-preview`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "api-key": process.env.AZURE_OPENAI_KEY
        },
        body: JSON.stringify(payload)
      }
    );

    const data = await response.json();

    context.res = {
      status: 200,
      body: { answer: data.choices[0].message.content }
    };

  } catch (err) {
    context.log.error(err);
    context.res = { status: 500, body: "Error interno" };
  }
}

