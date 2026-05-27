"""Gemini chat-session wrapper.

Per landing-page session we keep a persistent google-genai chat so subsequent
sections see the prior conversation (mirrors the user's manual Gemini Canvas
workflow). The model returns JSON with the full updated HTML plus a list of
image specs; assets are generated separately with gemini-2.5-flash-image.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from google import genai
from google.genai import types


TEXT_MODEL = "gemini-2.5-flash"
#TEXT_MODEL = "gemini-3.5-flash"
IMAGE_MODEL = "gemini-2.5-flash-image"

# Two architectural rules. Everything else (fonts, colors, framework, layout)
# is left to the model so it can faithfully clone the screenshot. The proven
# user prompt carries the role-setting and creative direction.
SYSTEM_INSTRUCTION = """REGLA 1 — IDIOMA OBLIGATORIO ESPAÑOL:
Todo el contenido textual del HTML que generes debe estar en ESPAÑOL neutro (Latam/España), SIN EXCEPCIONES. Si el screenshot que recibes está en otro idioma (portugués, inglés, francés, italiano, etc.), TRADUCE TODO el texto al español antes de escribirlo en el HTML. Esto aplica a:
- Títulos, subtítulos, párrafos, listas
- Botones, CTAs, enlaces y textos de navegación
- Etiquetas, placeholders y mensajes de formularios
- Footer, copyright, legales
- Atributos alt de las imágenes
- Etiquetas <title> y meta description
- Cualquier microcopy visible al usuario

NUNCA dejes texto en el idioma original del screenshot, NUNCA mezcles idiomas en la misma sección, y NUNCA copies literalmente palabras en portugués/inglés/etc. aunque la marca original las use (tradúcelas o adáptalas al español). Si una palabra extranjera es un nombre propio o marca registrada (ej: "iPhone"), déjala tal cual; en cualquier otro caso, traduce.

REGLA 2 — IMÁGENES CON MARCADORES:
Para cada imagen que necesites incluir en el HTML, en el atributo src usa un marcador secuencial: __IMG_0__, __IMG_1__, __IMG_2__, etc. La numeración debe ser continua a lo largo de toda la conversación (si en la sección anterior llegaste a __IMG_3__, la siguiente imagen es __IMG_4__). NUNCA escribas URLs reales, base64, ni rutas externas en el src — solo el marcador. El backend reemplazará cada marcador por la URL real en S3 después de generar la imagen con gemini-2.5-flash-image (nano banana)."""


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "html": {
            "type": "STRING",
            "description": "Documento HTML completo actualizado (con scaffolding en la primera sección, acumulado en las siguientes).",
        },
        "images": {
            "type": "ARRAY",
            "description": "Lista ordenada de imágenes a generar. El índice corresponde al marcador __IMG_N__ en el HTML.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "prompt": {
                        "type": "STRING",
                        "description": "Prompt detallado en INGLÉS para generar la imagen (estilo fotográfico, composición, paleta).",
                    },
                    "aspect_ratio": {
                        "type": "STRING",
                        "description": "Uno de: 1:1, 3:4, 4:3, 16:9, 9:16",
                    },
                },
                "required": ["prompt", "aspect_ratio"],
            },
        },
    },
    "required": ["html", "images"],
}


@dataclass
class SectionResult:
    html: str
    image_prompts: list[dict]  # [{"prompt": str, "aspect_ratio": str}, ...]


class GeminiSession:
    """One chat per landing-page session, preserving multi-turn context."""

    def __init__(self, client: genai.Client):
        self._chat = client.chats.create(
            model=TEXT_MODEL,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
                temperature=0.4,
            ),
        )
        self._client = client
        self._section_count = 0

    def add_section(self, image_bytes: bytes, brand_prompt: str = "") -> SectionResult:
        self._section_count += 1
        is_first = self._section_count == 1

        if is_first:
            user_text = """\
# ROLE AND CONTEXT
You are an Expert Frontend Developer, UI/UX Designer, and Localization Specialist. Your goal is to perfectly replicate the first section of the landing page shown in the attached image, achieving absolute pixel-perfect fidelity while fully translating all content into Spanish.

# TASK OVERVIEW
You will analyze the attached image, extract all layout, styling, and text data, and generate responsive HTML and CSS. You will also prepare image generation and handling instructions for NanoBanana and AWS S3.

# Critical Rules - NO EXCEPTIONS
1. DO NOT add, remove, or rearrange any visual element.
2. DO NOT change colors, spacing, typography, or layout structure.
3. DO NOT invent new sections, buttons, or decorative elements.
4. The final HTML must be visually identical to the attached image for the first section.
5. All visible text MUST be translated to Spanish (if not already Spanish).
6. Generate the image prompt using strict literal descriptions.
7. Do not invent new background colors.
8. Extract the exact hex codes from the reference image and include them in the prompt.
9. Add instructions to preserve the exact original shapes, clothing color, and background geometry.

Execute this task step-by-step:

## STEP 1: VISUAL ANALYSIS & EXTRACTION
Before writing any code, analyze the attached image and mentally map the following:
* **Images:** Identify all images. Classify each as a video preview, ebook mockup, person's face, full person, or background element.
* **Typography:** Identify titles, subtitles, and body text. Estimate the exact font family, font size, font weight, and font color for each.
* **Styling:** Identify background styles (multi-color gradients, solid colors, curved shapes/dividers).
* **Components:** Locate any Call-to-Action (CTA) buttons or countdown clocks.
* **Layout:** Map the exact positions, alignment, heights, aspect ratios, and pixel distances (padding/margin) between all elements.

## STEP 2: TRANSLATION & ASSET PREPARATION
* **Translation:** Translate ALL extracted text (titles, subtitles, buttons, and extra text) into natural-sounding Spanish.
* **Image Text:** If any image contains embedded text, provide the Spanish translation for that text.
* **Asset Pipeline:** For all identified images, generate the NanoBanana parameters required to replicate them. Assume these generated assets will be uploaded to S3 and use absolute S3 URLs (or structured placeholders) in your final HTML.

## STEP 3: CODE GENERATION
Write the clean, semantic HTML and CSS to replicate the design.
* **Fidelity:** The structure, layout, typography, and colors must be an exact match to the provided image. Do not add, alter, or remove any design elements. The resulting landing page has perfect pixel-perfect fidelity.
* **Responsiveness:** Implement High-Level UX Design. Ensure mobile adaptability where columns stack smoothly, text scales for perfect readability, and there is absolutely zero horizontal scrolling. Maintain aspect ratios and spacing across breakpoints.

# CONSTRAINTS & RULES
1. **Fidelity over everything:** The final visual output must look exactly like the input image, just translated into Spanish.
2. **No Hallucinations:** Do not invent extra sections, links, or footer elements that are not present in the attached image.
3. **Code Output:** Provide the final HTML and CSS in the json response.\
"""
            if brand_prompt.strip():
                user_text += f"\n\nContexto adicional de marca: {brand_prompt.strip()}"
        else:
            user_text = (
                "Ahora agrega la siguiente sección debajo de la anterior. "
                "Debes agregarla en español, como todo el documento debe estar en español."
            )
            if brand_prompt.strip():
                user_text += f"\n\nContexto adicional: {brand_prompt.strip()}"

        parts = [
            user_text,
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
        ]
        response = self._chat.send_message(parts)
        payload = json.loads(response.text)
        return SectionResult(
            html=payload["html"],
            image_prompts=payload.get("images", []),
        )

    def generate_image(self, prompt: str, aspect_ratio: str = "1:1") -> bytes:
        """Generate a single image via gemini-2.5-flash-image. Returns PNG bytes."""
        response = self._client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[f"{prompt}\n\nAspect ratio: {aspect_ratio}. Output: a single photographic image, no text overlay."],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                return part.inline_data.data
        raise RuntimeError(f"Gemini image model returned no image for prompt: {prompt[:80]}")


def build_client() -> genai.Client:
    api_key = os.environ["GEMINI_API_KEY"]
    return genai.Client(api_key=api_key)
