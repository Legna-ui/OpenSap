"""OCR with Tesseract (screen, regions and image files). Public functions live in api.py."""

from .api import (
    extraer_numeros,
    ocr_buscar,
    ocr_clic,
    ocr_disponible,
    ocr_esperar_texto,
    ocr_extraer,
    ocr_lineas,
    ocr_numero,
    ocr_numeros,
    ocr_palabras,
    ocr_texto,
)

__all__ = [
    "ocr_texto", "ocr_lineas", "ocr_numeros", "ocr_numero", "ocr_extraer", "ocr_palabras",
    "ocr_buscar", "ocr_esperar_texto", "ocr_clic", "ocr_disponible", "extraer_numeros",
]
