import { app } from "@azure/functions";

app.http("chat", {
  methods: ["POST"],
  authLevel: "anonymous",
  handler: async (request, context) => {
    context.log("Chat function invoked");

    const body = await request.json().catch(() => ({}));
    const message = body.message;

    if (!message) {
      return {
        status: 400,
        jsonBody: { error: "message is required" }
      };
    }

    return {
      status: 200,
      jsonBody: {
        answer: `Recibí tu mensaje: ${message}`
      }
    };
  }
});

