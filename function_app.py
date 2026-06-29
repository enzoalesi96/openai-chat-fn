import json
import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

CORS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

@app.route(route="ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"ok": True, "mensaje": "funciona"}),
        status_code=200,
        headers=CORS,
        mimetype="application/json",
    )