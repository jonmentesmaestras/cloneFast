"""Gemini chat-session wrapper.

Per landing-page session we keep a persistent google-genai chat so subsequent
sections see the prior conversation (mirrors the user's manual Gemini Canvas
workflow). The model returns JSON with the full updated HTML plus a list of
image specs; assets are generated separately with gemini-2.5-flash-image.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

from google import genai
from google.genai import types
from openai import OpenAI


TEXT_MODEL = "gemini-2.5-flash"
#TEXT_MODEL = "gemini-3.5-flash"
IMAGE_MODEL = "gpt-image-2"

_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":  "1024x1024",
    "4:3":  "1536x1024",
    "16:9": "2048x1152",
    "3:4":  "1024x1536",
    "9:16": "1024x1536",   # approx 2:3 — cheaper than 4K 2160x3840
}

_openai_client: OpenAI | None = None


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client

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
Para cada imagen que necesites incluir en el HTML, en el atributo src usa un marcador secuencial: __IMG_0__, __IMG_1__, __IMG_2__, etc. La numeración es continua y el prompt del usuario te indicará exactamente desde qué número debes empezar. NUNCA escribas URLs reales, base64, ni rutas externas en el src — solo el marcador. NUNCA uses el mismo marcador dos veces en el mismo fragmento. El número de specs en "images" debe coincidir EXACTAMENTE con el número de marcadores únicos en tu HTML.

REGLA 3 — SENTINEL DE SECCIÓN (OBLIGATORIO):
Envuelve el HTML de CADA sección con exactamente estos comentarios HTML en líneas propias:
<!-- SECTION_START -->
[HTML de la sección]
<!-- SECTION_END -->
Primera sección: el scaffold del documento (<html><head><body></body></html>) va FUERA de los sentinels. Solo el contenido visible de la primera sección va DENTRO.
Secciones siguientes: devuelve ÚNICAMENTE el fragmento de la nueva sección envuelto en los sentinels — SIN <html>, SIN <head>, SIN <body>, SIN HTML de secciones anteriores. NUNCA documento completo en secciones 2+."""


RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "html": {
            "type": "STRING",
            "description": "Primera sección: documento HTML completo donde el contenido de la primera sección está envuelto entre <!-- SECTION_START --> y <!-- SECTION_END -->. Secciones siguientes: SOLO el fragmento <section>...</section> de esta nueva sección (más <style> necesarios), envuelto entre <!-- SECTION_START --> y <!-- SECTION_END -->. NUNCA documento completo en secciones 2+. NUNCA el mismo __IMG_N__ dos veces. El número de specs en 'images' debe ser IGUAL al número de marcadores únicos en el HTML.",
        },
        "images": {
            "type": "ARRAY",
            "description": "Lista ordenada de imágenes a generar. El índice corresponde al marcador __IMG_N__ en el HTML.",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "prompt": {
                        "type": "STRING",
                        "description": "Descripción LITERAL y detallada (en INGLÉS) del contenido VISIBLE en el screenshot adjunto que corresponde a esta imagen específica. Incluye sujetos exactos (personas, objetos, productos), composición, colores hex extraídos del screenshot, iluminación, estilo fotográfico. NUNCA inventes contenido que no se vea en el screenshot; NUNCA reutilices descripciones de imágenes de secciones anteriores.",
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

    def add_section(self, image_bytes: bytes, brand_prompt: str = "", next_marker_index: int = 0) -> SectionResult:
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
3. **Code Output:** Provide the final HTML and CSS in the json response.

## SENTINEL & MARKER RULES — MANDATORY
- Wrap ONLY the first section's visible content (the <section> block) between:
  <!-- SECTION_START -->
  [first section content here]
  <!-- SECTION_END -->
- The full document scaffold (<html>, <head>, <body>, </body>, </html>) goes OUTSIDE these sentinel comments.
- Image markers in this section start at __IMG_0__.
- Every image slot must have a UNIQUE marker. NEVER use the same __IMG_N__ twice.\
"""
            if brand_prompt.strip():
                user_text += f"\n\nContexto adicional de marca: {brand_prompt.strip()}"
        else:
            user_text = f"""\
# CONTEXT
You are continuing the same landing page. This is **section #{self._section_count}**. The full HTML document is being assembled by the backend — you only need to return THIS section's fragment.

# OUTPUT FORMAT — MANDATORY
Return ONLY the HTML fragment for this new section, wrapped in sentinel comments:
<!-- SECTION_START -->
[this section's HTML: a <section>...</section> block plus any <style> rules it needs]
<!-- SECTION_END -->
Do NOT include <html>, <head>, <body>, </body>, or </html>.
Do NOT re-emit any HTML from prior sections.
Image markers for THIS section start at __IMG_{next_marker_index}__.

# Critical Rules - NO EXCEPTIONS
1. Analyze ONLY the attached screenshot. Do NOT reuse, copy, or paraphrase any content from prior sections.
2. DO NOT add, remove, or rearrange visual elements not present in the attached screenshot.
3. DO NOT change colors, spacing, typography, or layout structure shown in the attached screenshot.
4. DO NOT invent buttons, images, or decorative elements not in the attached screenshot.
5. All visible text MUST be translated to Spanish (if not already Spanish).
6. Image prompts MUST literally describe ONLY what is VISIBLE in the attached screenshot — NEVER recycle or paraphrase prompts from earlier sections.
7. Each <img> tag in your fragment MUST have a UNIQUE marker starting at __IMG_{next_marker_index}__. NEVER use the same __IMG_N__ twice.
8. The number of image specs in "images" MUST exactly equal the number of unique __IMG_N__ markers in your HTML fragment.
9. Extract exact hex codes from the attached screenshot. Do not invent new background colors.
10. Preserve exact shapes, clothing colors, and background geometry visible in the screenshot.

Execute step-by-step:

## STEP 1: VISUAL ANALYSIS & EXTRACTION (attached screenshot ONLY)
Before writing any code, analyze the attached image and mentally map:
* **Images:** Identify every image in THIS screenshot. Classify each (video preview, ebook mockup, person's face, full person, background element, icon).
* **Typography:** Identify titles, subtitles, body text. Estimate exact font family, size, weight, color.
* **Styling:** Identify background styles (gradients, solid colors, curved dividers).
* **Components:** Locate any CTA buttons, countdown clocks, or interactive elements.
* **Layout:** Map exact positions, alignment, heights, aspect ratios, and pixel distances (padding/margin).

## STEP 2: INVENTORY & ASSET PREPARATION
* **Translation:** Translate ALL extracted text to natural-sounding Spanish.
* **Image inventory:** Count every distinct image slot in THIS screenshot. Assign each a unique marker: __IMG_{next_marker_index}__, __IMG_{next_marker_index + 1}__, etc. Make a mental table: slot → marker → prompt.
* **Image prompts:** For EACH slot, write a literal English description of exactly what is VISIBLE in THIS screenshot — subjects, composition, exact hex colors, lighting, photographic style. NEVER recycle prompts from earlier sections.

## STEP 3: CODE GENERATION (fragment only)
Write the HTML fragment for this section only.
* **Fidelity:** Structure, layout, typography, and colors must exactly match the attached screenshot.
* **Marker integrity:** Every unique image slot gets a unique marker. Zero duplicate markers. Spec count equals unique marker count.
* **No prior section content:** Your output contains ONLY this section's new HTML wrapped in sentinels.
* **Responsiveness:** Columns stack smoothly, text scales, zero horizontal scroll, aspect ratios preserved.

# CONSTRAINTS
1. **Fragment only:** Your `html` field contains ONLY the sentinel-wrapped fragment — no full document.
2. **Fidelity:** The new section must look exactly like the attached screenshot, translated to Spanish.
3. **No hallucinations:** Do not invent elements not present in the attached screenshot.\
"""
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
        """Generate a single image via OpenAI gpt-image-2. Returns PNG bytes."""
        size = _ASPECT_TO_SIZE.get(aspect_ratio, "1024x1024")
        resp = _get_openai().images.generate(
            model=IMAGE_MODEL,
            prompt=f"{prompt}\n\nA single photographic image, no text overlay.",
            size=size,
            n=1,
        )
        b64 = resp.data[0].b64_json
        if not b64:
            raise RuntimeError(f"OpenAI {IMAGE_MODEL} returned no image for prompt: {prompt[:80]}")
        return base64.b64decode(b64)


def build_client() -> genai.Client:
    api_key = os.environ["GEMINI_API_KEY"]
    return genai.Client(api_key=api_key)
