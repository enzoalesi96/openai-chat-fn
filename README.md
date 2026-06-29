# VisionMatch AI — Azure Function (Python)

Chatbot de recomendación de televisores migrado de **Node.js → Python**.

---

## Arquitectura

```
Usuario (browser)
       │  POST /api/chat  { "message": "55 pulgadas QLED hasta 3000 soles" }
       ▼
┌──────────────────────────────┐
│   Azure Static Web App       │  https://salmon-pond-029452710.7.azurestaticapps.net
└──────────────┬───────────────┘
               │ fetch /api/chat
               ▼
┌──────────────────────────────┐
│   Azure Function (Python)    │  function_app.py
│   route: /api/chat           │
│                              │
│  1. Valida mensaje           │
│  2. Extrae parámetros        │
│  3. → Model Serving endpoint │──► visionmatch-kmeans → cluster_id
│  4. → SQL Statement API      │──► data_oh_completa (Delta Lake) → top-5
│  5. → Azure OpenAI           │──► gpt-4o-mini → respuesta natural
│  6. ← JSON al frontend       │
└──────────────────────────────┘

Databricks:
  Serving endpoint : https://adb-7405610080077220.0.azuredatabricks.net
                     /serving-endpoints/visionmatch-kmeans/invocations
  Tabla fuente     : adbvisualmatch_7405610080077220.schema_tv.data_oh_completa
```

---

## Estructura del proyecto

```
visionmatch-fn-python/
├── function_app.py           # Entry point (Python v2 programming model)
├── shared/
│   ├── __init__.py
│   ├── helpers.py            # Parsing de mensajes, limpieza de precios, CORS
│   ├── databricks_client.py  # Model Serving endpoint + SQL Statement API
│   └── aoai_client.py        # Azure OpenAI gpt-4o-mini
├── host.json
├── requirements.txt
└── local.settings.json       # Solo para desarrollo local (NO subir a git)
```

---

## Variables de entorno requeridas

| Variable                  | Descripción                                                       |
|---------------------------|-------------------------------------------------------------------|
| `AOAI_ENDPOINT`           | `https://<recurso>.openai.azure.com/`                             |
| `AOAI_API_KEY`            | API key de Azure OpenAI                                           |
| `AOAI_DEPLOYMENT`         | Nombre del deployment, ej: `gpt-4o-mini`                         |
| `DATABRICKS_TOKEN`        | Personal Access Token del workspace                               |
| `DATABRICKS_WAREHOUSE_ID` | *(opcional)* ID del SQL Warehouse; si no se define, lo detecta solo |
| `STATIC_WEB_ORIGIN`       | Origen CORS del Static Web App                                    |

> **Ya no se necesitan** `DATABRICKS_HOST`, `DATABRICKS_JOB_ID` ni `DATABRICKS_WAREHOUSE_ID` para lanzar Jobs.
> El endpoint de Model Serving tiene su URL hardcodeada en `databricks_client.py`.

---

## Instalación y desarrollo local

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
func start
```

### Ejemplo de llamada

```bash
curl -X POST http://localhost:7071/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "55 pulgadas QLED hasta 3000 soles"}'
```

Respuesta:
```json
{
  "message": "📺 Te recomiendo el Samsung 55\" QLED ...",
  "products": [
    { "name": "...", "familia": "QLED", "pulgadas": "55",
      "precio": "2799.0", "vendedor": "Oechsle", "url": "...", "imagen": "..." }
  ]
}
```

---

## Flujo detallado

```
Mensaje usuario
    │
    ├─► has_enough_info()     ¿pulgadas + presupuesto + familia? → si no, pide datos
    ├─► extract_params()      inches=55, budget=3000.0, family="QLED"
    │
    ├─► predict_cluster()     POST /serving-endpoints/visionmatch-kmeans/invocations
    │     payload: { "dataframe_records": [{"pulgadas":55,"precio":3000,"familia_num":1.0}] }
    │     response: { "predictions": [2] }  → cluster_id = 2
    │
    ├─► fetch_top5()          SQL sobre data_oh_completa filtrado por cluster + presupuesto
    │     · scoring: (precio/presupuesto)*100 − |pulgadas−ref|*2
    │     · si < 5 candidatos → fallback a toda la familia
    │     · devuelve hasta 5 filas
    │
    └─► generate_recommendation()
          POST Azure OpenAI gpt-4o-mini con top-5 + contexto
          → texto conversacional en español peruano
```

---

## Despliegue en Azure

```bash
func azure functionapp publish <FUNCTION_APP_NAME> --python

az functionapp config appsettings set \
  --name <FUNCTION_APP_NAME> \
  --resource-group <RG> \
  --settings \
    AOAI_ENDPOINT="https://..." \
    AOAI_API_KEY="..." \
    AOAI_DEPLOYMENT="gpt-4o-mini" \
    DATABRICKS_TOKEN="..." \
    DATABRICKS_WAREHOUSE_ID="..." \
    STATIC_WEB_ORIGIN="https://salmon-pond-029452710.7.azurestaticapps.net"
```
