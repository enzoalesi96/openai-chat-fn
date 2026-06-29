"""
shared/helpers.py
-----------------
Utilidades compartidas para VisionMatch AI (Azure Functions Python).
"""

import re
import os
from typing import Optional


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

ALLOWED_ORIGIN = os.environ.get(
    "STATIC_WEB_ORIGIN",
    "https://salmon-pond-029452710.7.azurestaticapps.net",
)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ---------------------------------------------------------------------------
# Validación y extracción de parámetros del mensaje
# ---------------------------------------------------------------------------

# Mapeo normalizado de familia (igual que modelo_televisores.py)
FAMILY_ALIASES: dict[str, str] = {
    "led":      "LED",
    "qled":     "QLED",
    "oled":     "OLED",
    "nanocell": "NanoCell",
    "4k":       "LED",       # fallback razonable
    "full hd":  "LED",
    "fullhd":   "LED",
    "hd":       "LED",
}


def has_enough_info(message: str) -> bool:
    """Devuelve True si el mensaje contiene pulgadas, presupuesto y familia."""
    has_inches  = bool(re.search(r'\d+\s?(?:pulgadas|")', message, re.I))
    has_budget  = bool(re.search(r'\d{3,}', message))
    has_family  = bool(re.search(
        r'\b(?:led|qled|oled|nanocell|4k|full\s?hd|hd)\b', message, re.I
    ))
    return has_inches and has_budget and has_family


def extract_params(message: str) -> dict:
    """
    Extrae pulgadas, presupuesto y familia del texto libre del usuario.
    Devuelve dict con claves: inches (int), budget (float), family (str).
    """
    # Pulgadas
    m_inches = re.search(r'(\d+)\s?(?:pulgadas|")', message, re.I)
    inches = int(m_inches.group(1)) if m_inches else None

    # Presupuesto (primer número ≥ 3 dígitos, ignorando el de pulgadas)
    msg_clean = re.sub(r'(\d+)\s?(?:pulgadas|")', '', message, flags=re.I)
    m_budget  = re.search(r'(\d{3,}(?:[.,]\d+)?)', msg_clean)
    budget    = float(m_budget.group(1).replace(',', '.')) if m_budget else None

    # Familia
    m_family = re.search(
        r'\b(led|qled|oled|nanocell|4k|full\s?hd|hd)\b', message, re.I
    )
    raw_family = m_family.group(1).lower().replace(' ', '') if m_family else 'led'
    family = FAMILY_ALIASES.get(raw_family, "LED")

    return {"inches": inches, "budget": budget, "family": family}


# ---------------------------------------------------------------------------
# Limpieza de precios
# ---------------------------------------------------------------------------

def clean_price(value: Optional[str]) -> Optional[float]:
    """Elimina caracteres no numéricos y convierte a float. Devuelve None si inválido."""
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(value))
    try:
        result = float(cleaned)
        return result if result > 0 else None
    except ValueError:
        return None


def best_price(row: dict) -> Optional[float]:
    """Devuelve el precio mínimo válido entre los cuatro campos de precio."""
    candidates = [
        clean_price(row.get("internet_price")),
        clean_price(row.get("event_price")),
        clean_price(row.get("normal_price")),
        clean_price(row.get("cmr_price")),
    ]
    valid = [p for p in candidates if p is not None]
    return min(valid) if valid else None


# ---------------------------------------------------------------------------
# Extracción de pulgadas desde el nombre del producto
# ---------------------------------------------------------------------------

def extract_inches_from_name(name: str) -> Optional[int]:
    """Extrae el primer número de 2-3 dígitos que represente pulgadas."""
    m = re.search(r'\b([2-9][0-9]|1[0-9]{2})\b', name)
    return int(m.group(1)) if m else None
