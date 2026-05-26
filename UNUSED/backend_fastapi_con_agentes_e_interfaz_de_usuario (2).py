# -*- coding: utf-8 -*-
"""
Vision-to-Code Landing Page Builder - Prueba de Concepto (PoC)
Backend unificado con agentes de IA (Gemini & Imagen 4.0), Fusión DOM y UI interactiva.
"""

import os
import base64
import json
import logging
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
import httpx
from bs4 import BeautifulSoup

# Configuración de Logging en español
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LandingPageBuilder")

# Inicialización de la aplicación FastAPI
app = FastAPI(
    title="Vision-to-Code Landing Page Builder - API & UI",
    description="Backend de agentes de IA para construir landings a partir de capturas de pantalla.",
    version="1.0.0"
)

# Clave API de Gemini (Dejar en blanco para que el entorno provea la clave o leer de variable de entorno)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Estado de la base de datos en memoria (Simula la persistencia de la landing page)
DATABASE = {
    "current_html": ""
}

# --- MODELOS DE DATOS (PYDANTIC) ---

class AnalyzeRequest(BaseModel):
    image_base64: str = Field(..., description="Imagen de la sección codificada en Base64 sin prefijo data:image")
    brand_prompt: Optional[str] = Field("", description="Indicaciones adicionales de marca o tono de voz por el usuario")

class GenerateAssetRequest(BaseModel):
    prompt: str = Field(..., description="Prompt descriptivo en inglés para alimentar a Imagen 4.0")
    aspect_ratio: str = Field("1:1", description="Relación de aspecto deseada para la imagen")

class GenerateSectionRequest(BaseModel):
    analysis_json: dict = Field(..., description="JSON estructurado proveniente del Agente de Visión")
    assets_mapping: dict = Field(..., description="Mapeo de IDs de imagen a sus respectivas URLs o Data-URIs de alta calidad")

class MergeRequest(BaseModel):
    new_section_html: str = Field(..., description="Bloque de código HTML de la sección recién generada")


# --- FUNCIONES DE ASISTENCIA / PIPELINE DE GEMINI ---

async def call_gemini_text_api(system_prompt: str, user_prompt: str, response_schema: Optional[dict] = None) -> str:
    """
    Realiza llamadas robustas no-streaming al modelo gemini-2.5-flash-preview-09-2025
    con soporte para esquemas JSON estructurados y reintentos (Exponential Backoff).
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{"text": user_prompt}]
        }],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        }
    }
    
    if response_schema:
        payload["generationConfig"] = {
            "responseMimeType": "application/json",
            "responseSchema": response_schema
        }
        
    retries = 5
    delay = 1.0
    
    async with httpx.AsyncClient() as client:
        for i in range(retries):
            try:
                response = await client.post(url, json=payload, timeout=60.0)
                if response.status_code == 200:
                    result = response.json()
                    text_content = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    return text_content
                else:
                    logger.warning(f"Error en Gemini API (Intento {i+1}): HTTP {response.status_code} - {response.text}")
            except Exception as e:
                logger.error(f"Excepción en llamada de red de Gemini (Intento {i+1}): {str(e)}")
            
            # Reintento con backoff exponencial
            import asyncio
            await asyncio.sleep(delay)
            delay *= 2.0
            
        raise HTTPException(status_code=500, detail="La API de Gemini no respondió después de múltiples intentos.")


async def call_gemini_vision_api(image_base64: str, system_prompt: str, user_prompt: str, response_schema: dict) -> dict:
    """
    Envía una imagen Base64 junto con prompts al modelo gemini-2.5-flash-preview-09-2025 
    para extraer tokens de diseño y especificaciones JSON estructuradas.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    
    # Sanitizar prefijos comunes de base64 si están presentes
    if "," in image_base64:
        image_base64 = image_base64.split(",")[1]
        
    payload = {
        "contents": [{
            "parts": [
                {"text": user_prompt},
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": image_base64
                    }
                }
            ]
        }],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=60.0)
            if response.status_code == 200:
                result = response.json()
                text_content = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                return json.loads(text_content)
            else:
                raise HTTPException(status_code=response.status_code, detail=f"Error en Gemini Vision API: {response.text}")
        except Exception as e:
            logger.error(f"Error crítico en agente de visión: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Fallo crítico al conectar con el Agente de Visión: {str(e)}")


# --- ENDPOINTS DE LA API ---

@app.post("/api/analyze-vision", summary="Agente 1: Analizar Captura de Pantalla")
async def analyze_vision(request: AnalyzeRequest):
    """
    Analiza un segmento de captura de pantalla y retorna especificaciones de diseño estructuradas,
    paleta de colores, fuentes, textos extraídos y requerimientos de assets de imagen.
    """
    system_prompt = (
        "Actúa como un analista experto de interfaces UX/UI de nivel mundial. Tu tarea es deconstruir la imagen "
        "de la sección de landing page adjunta y estructurarla en un JSON detallado. "
        "Debes identificar la estructura visual, la tipografía idónea, los colores hexadecimales dominantes y todos los "
        "textos incluidos. Además, detecta qué imágenes se necesitan y describe detalladamente cómo deben ser generadas "
        "por una Inteligencia Artificial con descripciones fotográficas premium en inglés."
    )
    
    user_prompt = (
        f"Analiza esta captura de pantalla de sección. Aquí tienes guías adicionales del usuario: {request.brand_prompt}. "
        "Genera una estructura de datos estricta siguiendo el esquema JSON provisto."
    )
    
    # Esquema JSON requerido por Gemini
    schema = {
        "type": "OBJECT",
        "properties": {
            "seccion_tipo": { "type": "STRING", "description": "hero, cta, caracteristicas, faq, pricing, o testimonio" },
            "paleta_colores": {
                "type": "OBJECT",
                "properties": {
                    "fondo": { "type": "STRING", "description": "Color hex de fondo de la sección" },
                    "texto_principal": { "type": "STRING", "description": "Color hex del texto principal" },
                    "acentos": {
                        "type": "ARRAY",
                        "items": { "type": "STRING" },
                        "description": "Colores hex complementarios o de botones de acción"
                    }
                },
                "required": ["fondo", "texto_principal", "acentos"]
            },
            "tipografia": {
                "type": "OBJECT",
                "properties": {
                    "estilo": { "type": "STRING", "description": "serif, sans, o display" },
                    "fuente_sugerida": { "type": "STRING", "description": "Nombre de Google Font sugerida, ej: Montserrat" }
                },
                "required": ["estilo", "fuente_sugerida"]
            },
            "texto_extraido": {
                "type": "OBJECT",
                "properties": {
                    "titulo": { "type": "STRING" },
                    "subtitulo": { "type": "STRING" },
                    "parrafos": {
                        "type": "ARRAY",
                        "items": { "type": "STRING" }
                    },
                    "cta_text": { "type": "STRING", "description": "Texto del botón principal si aplica" }
                },
                "required": ["titulo", "subtitulo", "parrafos", "cta_text"]
            },
            "requerimiento_imagenes": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": { "type": "STRING", "description": "Identificador único de la imagen requerida, ej: img_producto_1" },
                        "descripcion_para_generar": { "type": "STRING", "description": "Detallado prompt fotográfico descriptivo en inglés para Imagen 4.0, ej: 'A close-up premium photo of a clinical facial peeling application, warm lighting, elegant...'" },
                        "keywords_stock": {
                            "type": "ARRAY",
                            "items": { "type": "STRING" },
                            "description": "Conceptos clave para buscar alternativas en galerías de stock"
                        },
                        "aspect_ratio": { "type": "STRING", "description": "3:4, 16:9, o 1:1" },
                        "estilo_visual": { "type": "STRING", "description": "fotografia_clinica, minimalista_vectorial, o retrato" }
                    },
                    "required": ["id", "descripcion_para_generar", "keywords_stock", "aspect_ratio", "estilo_visual"]
                }
            }
        },
        "required": ["seccion_tipo", "paleta_colores", "tipografia", "texto_extraido", "requerimiento_imagenes"]
    }
    
    result_json = await call_gemini_vision_api(request.image_base64, system_prompt, user_prompt, schema)
    return JSONResponse(content=result_json)


@app.post("/api/generate-asset", summary="Agente 2: Generación Esteticista de Imágenes (Imagen 4.0)")
async def generate_asset(request: GenerateAssetRequest):
    """
    Utiliza el modelo oficial 'imagen-4.0-generate-001' para pintar recursos e imágenes de alta gama,
    retornando un Data-URI Base64 perfectamente renderizable en cualquier navegador sin enlaces rotos.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict?key={GEMINI_API_KEY}"
    
    payload = {
        "instances": [
            { "prompt": request.prompt }
        ],
        "parameters": {
            "sampleCount": 1,
            "aspectRatio": request.aspect_ratio,
            "outputMimeType": "image/png"
        }
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, timeout=60.0)
            if response.status_code == 200:
                result = response.json()
                # Extraer bytes codificados en base64 de la predicción de Imagen 4.0
                base64_encoded = result["predictions"][0]["bytesBase64Encoded"]
                image_url = f"data:image/png;base64,{base64_encoded}"
                return {"secure_url": image_url}
            else:
                logger.warning(f"Fallo en llamada a Imagen 4.0, se utilizará fallback premium. Detalle: {response.text}")
                # Fallback elegante usando Unsplash aleatorio temático de alta gama para mantener robustez en la PoC
                import random
                rand_num = random.randint(1, 1000)
                fallback_url = f"https://images.unsplash.com/photo-1512290923902-8a9f81dc236c?w=600&auto=format&fit=crop&q=80&rand={rand_num}"
                return {"secure_url": fallback_url}
        except Exception as e:
            logger.error(f"Error llamando al Agente de Assets: {str(e)}")
            # Fallback elegante a Unsplash
            import random
            rand_num = random.randint(1, 1000)
            fallback_url = f"https://images.unsplash.com/photo-1570172619644-dfd03ed5d881?w=600&auto=format&fit=crop&q=80&rand={rand_num}"
            return {"secure_url": fallback_url}


@app.post("/api/generate-html-section", summary="Agente 3: Redacción Estética de Código HTML")
async def generate_html_section(request: GenerateSectionRequest):
    """
    Toma los datos de diseño analizados por el Agente de Visión y las imágenes resueltas para
    escribir una sección HTML altamente interactiva, responsiva y semántica en español usando Tailwind CSS.
    """
    system_prompt = (
        "Actúa como un desarrollador frontend senior especializado en la maquetación de landing pages de conversión y estética. "
        "Tu misión es escribir únicamente un bloque de código HTML semántico correspondiente a la sección solicitada. "
        "Debes utilizar Tailwind CSS nativo para todo el estilado, fuentes de Google Fonts y Lucide Icons para la iconografía. "
        "Todo el texto y mensajes deben estar rigurosamente redactados en un español profesional y fluido. "
        "Asegúrate de inyectar las URLs de imágenes mapeadas en sus respectivos elementos <img>. "
        "No incluyas explicaciones de código ni envolturas Markdown como ```html. Devuelve estrictamente un objeto JSON estructurado."
    )
    
    user_prompt = (
        f"Genera la sección HTML basándote en este análisis visual: {json.dumps(request.analysis_json)}. "
        f"Mapea y reemplaza los marcadores de posición de las imágenes con estas URLs reales de assets: {json.dumps(request.assets_mapping)}. "
        "Escribe la sección con gran atención al detalle, espaciado premium, interactividad sutil en botones (como hover y transiciones activas) y diseño fluido móvil-escritorio."
    )
    
    schema = {
        "type": "OBJECT",
        "properties": {
            "html_code": { 
                "type": "STRING", 
                "description": "Bloque puro de código HTML de la sección (ej. un tag <section> o <main>) con clases Tailwind" 
            }
        },
        "required": ["html_code"]
    }
    
    result_text = await call_gemini_text_api(system_prompt, user_prompt, response_schema=schema)
    result_json = json.loads(result_text)
    return JSONResponse(content=result_json)


@app.post("/api/merge-html", summary="Agente 4: Compilador Quirúrgico DOM")
async def merge_html(request: MergeRequest):
    """
    Inyecta la sección HTML recién generada en el documento de trabajo actual usando BeautifulSoup,
    creando el scaffolding inicial si se trata de la primera sección.
    """
    current_html = DATABASE["current_html"]
    
    if not current_html or "landing-builder-sections" not in current_html:
        # Generar scaffolding base inicial de la landing page si está vacía
        soup = BeautifulSoup(
            """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Landing Page Maquetada con IA</title>
    <!-- Tailwind CSS -->
    <script src="[https://cdn.tailwindcss.com](https://cdn.tailwindcss.com)"></script>
    <!-- Google Fonts -->
    <link rel="preconnect" href="[https://fonts.googleapis.com](https://fonts.googleapis.com)">
    <link rel="preconnect" href="[https://fonts.gstatic.com](https://fonts.gstatic.com)" crossorigin>
    <link href="[https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;700;900&family=Plus+Jakarta+Sans:wght@300;400;600;800&display=swap](https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;700;900&family=Plus+Jakarta+Sans:wght@300;400;600;800&display=swap)" rel="stylesheet">
    <!-- Lucide Icons -->
    <script src="[https://unpkg.com/lucide@latest](https://unpkg.com/lucide@latest)"></script>
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
    </style>
</head>
<body class="bg-[#FAF6F0] text-slate-800 min-h-screen">
    
    <!-- Contenedor Maestre de Secciones -->
    <div id="landing-builder-sections">
        <!-- SECTIONS_PLACEHOLDER -->
    </div>

    <script>
        // Inicializar iconos de Lucide cargados dinámicamente
        lucide.createIcons();
    </script>
</body>
</html>""", "html_parser"
        )
    else:
        soup = BeautifulSoup(current_html, "html_parser")
        
    # Encontrar el contenedor de secciones maestro
    container = soup.find(id="landing-builder-sections")
    if not container:
        raise HTTPException(status_code=500, detail="Estructura de la Landing Page alterada, falta contenedor raíz.")
        
    # Parsear el HTML de la nueva sección
    new_section_soup = BeautifulSoup(request.new_section_html, "html_parser")
    
    # Inyectar el fragmento al final de las secciones
    container.append(new_section_soup)
    
    # Asegurar que se vuelvan a registrar los iconos de Lucide agregados en la sección
    if soup.body and not soup.find(id="lucide-reinit-script"):
        reinit_script = soup.new_tag("script", id="lucide-reinit-script")
        reinit_script.string = "lucide.createIcons();"
        soup.body.append(reinit_script)
        
    # Guardar estado consolidado
    DATABASE["current_html"] = str(soup)
    
    return {"status": "success", "total_html": DATABASE["current_html"]}


@app.get("/api/workspace", summary="Consultar Landing Consolidada")
async def get_workspace():
    """Retorna el código HTML completo consolidado en tiempo real."""
    return {"html": DATABASE["current_html"]}


@app.post("/api/workspace/reset", summary="Reiniciar Espacio de Trabajo")
async def reset_workspace():
    """Limpia el código de la landing page para iniciar una nueva creación."""
    DATABASE["current_html"] = ""
    return {"status": "reset_successful"}


# --- INTERFAZ WEB EMBEBIDA (UI) ---

@app.get("/", response_class=HTMLResponse, summary="Interfaz de Usuario PoC")
async def serve_ui():
    """
    Sirve una UI interactiva espectacular construida con Tailwind CSS y Vanilla JS.
    Permite cargar capturas de pantalla, ingresar prompts y ver interactivamente el
    proceso de los Agentes de Visión, Assets y Fusión HTML.
    """
    return """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vision-to-Code Agent Studio</title>
    <!-- Tailwind CSS -->
    <script src="[https://cdn.tailwindcss.com](https://cdn.tailwindcss.com)"></script>
    <!-- Lucide Icons -->
    <script src="[https://unpkg.com/lucide@latest](https://unpkg.com/lucide@latest)"></script>
    <link href="[https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap](https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap)" rel="stylesheet">
    <style>
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
        }
    </style>
</head>
<body class="bg-[#FAF6F0] text-slate-800 min-h-screen flex flex-col">

    <!-- Header -->
    <header class="bg-white border-b border-slate-100 py-4 px-6 md:px-12 flex justify-between items-center shadow-xs">
        <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-md">
                <i data-lucide="sparkles" class="w-5 h-5"></i>
            </div>
            <div>
                <h1 class="font-bold text-base text-slate-900 leading-none">Vision-to-Code</h1>
                <p class="text-[10px] text-slate-400 font-semibold tracking-wider uppercase mt-0.5">Estudio de Agentes IA</p>
            </div>
        </div>
        <div class="flex items-center gap-3">
            <button onclick="resetWorkspace()" class="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold text-rose-600 bg-rose-50 hover:bg-rose-100 rounded-xl transition">
                <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>
                <span>Reiniciar Lienzo</span>
            </button>
        </div>
    </header>

    <!-- Contenido Principal Split-Screen -->
    <main class="flex-1 grid grid-cols-1 lg:grid-cols-12 gap-6 p-6 md:p-8 max-w-[1800px] w-full mx-auto">
        
        <!-- Panel de Control Izquierdo (Entradas y Agentes) -->
        <div class="lg:col-span-5 flex flex-col space-y-6">
            
            <!-- Tarjeta 1: Carga de Sección de Captura -->
            <div class="bg-white rounded-2xl p-6 border border-slate-100 shadow-sm space-y-4">
                <div class="flex items-center gap-2">
                    <span class="w-6 h-6 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center text-xs font-bold">1</span>
                    <h2 class="font-bold text-slate-900 text-sm uppercase tracking-wide">Carga de Captura de Sección</h2>
                </div>
                
                <div id="dropzone" class="border-2 border-dashed border-slate-200 rounded-xl p-8 text-center hover:border-indigo-500 transition cursor-pointer relative bg-slate-50/50">
                    <input type="file" id="fileInput" class="hidden" accept="image/*" onchange="handleFileSelect(event)">
                    <div id="dropzone-prompt" class="space-y-2">
                        <i data-lucide="image" class="w-10 h-10 text-slate-400 mx-auto"></i>
                        <p class="text-xs font-semibold text-slate-600">Arrastra o selecciona la captura de tu sección</p>
                        <p class="text-[10px] text-slate-400">Formatos PNG, JPG de hasta 5MB</p>
                    </div>
                    <img id="imagePreview" class="hidden max-h-48 rounded-lg mx-auto object-contain border border-slate-100 shadow-xs" src="" alt="Vista previa">
                </div>

                <div class="space-y-2">
                    <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider">Instrucciones de Marca (Opcional)</label>
                    <textarea id="brandPrompt" class="w-full text-xs p-3 bg-slate-50 border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-500 h-20 resize-none text-slate-700" placeholder="Ej: Mantener una paleta rosa pastel y fuentes serif elegantes, enfocar la redacción en un spa clínico..."></textarea>
                </div>

                <button id="btnBuild" onclick="startAgentPipeline()" class="w-full bg-indigo-600 hover:bg-indigo-700 active:scale-95 text-white py-3.5 px-6 rounded-xl font-bold text-xs transition duration-300 flex items-center justify-center gap-2 shadow-lg shadow-indigo-600/20">
                    <i data-lucide="play" class="w-4 h-4 fill-white"></i>
                    <span>EJECUTAR AGENTES DE IA</span>
                </button>
            </div>

            <!-- Tarjeta 2: Monitor de Agentes en Tiempo Real -->
            <div class="bg-white rounded-2xl p-6 border border-slate-100 shadow-sm space-y-4 flex-1">
                <div class="flex items-center justify-between border-b border-slate-50 pb-3">
                    <div class="flex items-center gap-2">
                        <i data-lucide="activity" class="w-4 h-4 text-emerald-500"></i>
                        <h2 class="font-bold text-slate-900 text-sm uppercase tracking-wide">Monitor de Agentes</h2>
                    </div>
                    <span id="systemStatus" class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold bg-slate-100 text-slate-500 uppercase">
                        <span class="w-1.5 h-1.5 rounded-full bg-slate-400"></span>
                        Ocioso
                    </span>
                </div>

                <!-- Pipeline Steps -->
                <div class="space-y-4">
                    <!-- Paso 1: Visión -->
                    <div class="flex items-start gap-3 p-3 rounded-xl transition" id="step-vision">
                        <div class="step-icon p-2 rounded-lg bg-slate-100 text-slate-400 mt-0.5">
                            <i data-lucide="eye" class="w-4 h-4"></i>
                        </div>
                        <div class="flex-1 text-xs">
                            <div class="flex justify-between items-center">
                                <span class="font-bold text-slate-700">Agente 1: Análisis de Visión (Gemini)</span>
                                <span class="step-badge text-[10px] text-slate-400 font-semibold uppercase">Esperando</span>
                            </div>
                            <p class="text-slate-400 text-[11px] mt-0.5 step-desc">Descomponiendo layout, tipografías y textos de la captura.</p>
                        </div>
                    </div>

                    <!-- Paso 2: Assets -->
                    <div class="flex items-start gap-3 p-3 rounded-xl transition" id="step-assets">
                        <div class="step-icon p-2 rounded-lg bg-slate-100 text-slate-400 mt-0.5">
                            <i data-lucide="image" class="w-4 h-4"></i>
                        </div>
                        <div class="flex-1 text-xs">
                            <div class="flex justify-between items-center">
                                <span class="font-bold text-slate-700">Agente 2: Resolución de Assets (Imagen 4.0)</span>
                                <span class="step-badge text-[10px] text-slate-400 font-semibold uppercase">Esperando</span>
                            </div>
                            <p class="text-slate-400 text-[11px] mt-0.5 step-desc">Pintando y buscando fotografías de alta resolución en la nube.</p>
                        </div>
                    </div>

                    <!-- Paso 3: Código -->
                    <div class="flex items-start gap-3 p-3 rounded-xl transition" id="step-code">
                        <div class="step-icon p-2 rounded-lg bg-slate-100 text-slate-400 mt-0.5">
                            <i data-lucide="code-xml" class="w-4 h-4"></i>
                        </div>
                        <div class="flex-1 text-xs">
                            <div class="flex justify-between items-center">
                                <span class="font-bold text-slate-700">Agente 3: Redactor de Código HTML (Tailwind)</span>
                                <span class="step-badge text-[10px] text-slate-400 font-semibold uppercase">Esperando</span>
                            </div>
                            <p class="text-slate-400 text-[11px] mt-0.5 step-desc">Escribiendo la sección responsiva interactiva y mapeando imágenes.</p>
                        </div>
                    </div>

                    <!-- Paso 4: Fusión -->
                    <div class="flex items-start gap-3 p-3 rounded-xl transition" id="step-merge">
                        <div class="step-icon p-2 rounded-lg bg-slate-100 text-slate-400 mt-0.5">
                            <i data-lucide="combine" class="w-4 h-4"></i>
                        </div>
                        <div class="flex-1 text-xs">
                            <div class="flex justify-between items-center">
                                <span class="font-bold text-slate-700">Agente 4: Compilador Quirúrgico DOM</span>
                                <span class="step-badge text-[10px] text-slate-400 font-semibold uppercase">Esperando</span>
                            </div>
                            <p class="text-slate-400 text-[11px] mt-0.5 step-desc">Fusión sutil e inteligente de la sección en la landing acumulada.</p>
                        </div>
                    </div>
                </div>
            </div>

        </div>

        <!-- Panel de Visualización Derecho (Live Preview) -->
        <div class="lg:col-span-7 flex flex-col space-y-4">
            <div class="bg-white rounded-2xl border border-slate-100 shadow-sm overflow-hidden flex-1 flex flex-col min-h-[500px]">
                
                <!-- Barra del Navegador Virtual -->
                <div class="bg-slate-50 border-b border-slate-100 py-3 px-4 flex items-center justify-between gap-4">
                    <div class="flex items-center gap-1.5 flex-shrink-0">
                        <span class="w-3 h-3 rounded-full bg-rose-400 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-amber-400 inline-block"></span>
                        <span class="w-3 h-3 rounded-full bg-emerald-400 inline-block"></span>
                    </div>
                    <!-- Entrada de URL Falsa -->
                    <div class="flex-1 bg-white border border-slate-200/60 rounded-lg py-1 px-3 text-xs text-slate-400 flex items-center gap-2 max-w-lg select-none">
                        <i data-lucide="lock" class="w-3 h-3 text-emerald-500"></i>
                        <span>[https://mi-nueva-landing-page.ia/preview](https://mi-nueva-landing-page.ia/preview)</span>
                    </div>
                    <!-- Botón de descarga rápida del código consolidado -->
                    <button onclick="downloadCode()" class="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold text-indigo-600 hover:bg-indigo-50 border border-indigo-100 rounded-lg transition">
                        <i data-lucide="download" class="w-3.5 h-3.5"></i>
                        <span>Exportar HTML</span>
                    </button>
                </div>

                <!-- Iframe del Preview de la Landing -->
                <div class="flex-1 bg-slate-100 relative">
                    <iframe id="previewIframe" class="w-full h-full bg-white transition" src="about:blank"></iframe>
                    <div id="iframePlaceholder" class="absolute inset-0 flex flex-col items-center justify-center p-8 text-center bg-white space-y-3 z-10">
                        <div class="w-12 h-12 rounded-full bg-slate-50 flex items-center justify-center text-slate-400 border border-slate-100">
                            <i data-lucide="globe" class="w-6 h-6"></i>
                        </div>
                        <div>
                            <p class="text-xs font-bold text-slate-800">Lienzo de Trabajo Vacío</p>
                            <p class="text-[11px] text-slate-400 max-w-xs mx-auto mt-0.5">Sube la primera sección de tu captura y activa los agentes para pintar el contenido aquí.</p>
                        </div>
                    </div>
                </div>

            </div>
        </div>

    </main>

    <!-- Vanilla Javascript del Frontend de la PoC -->
    <script>
        lucide.createIcons();

        let base64Image = "";

        // Activar Input de archivo al hacer click en la dropzone
        const dropzone = document.getElementById('dropzone');
        dropzone.addEventListener('click', () => {
            document.getElementById('fileInput').click();
        });

        // Prevenir arrastres por defecto para permitir Drag & Drop
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, preventDefaults, false);
        });

        function preventDefaults (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        dropzone.addEventListener('drop', handleDrop, false);

        function handleDrop(e) {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files.length) {
                processFile(files[0]);
            }
        }

        function handleFileSelect(e) {
            const files = e.target.files;
            if (files.length) {
                processFile(files[0]);
            }
        }

        // Procesar archivo de imagen y transformarlo a Base64 sutilmente
        function processFile(file) {
            const reader = new FileReader();
            reader.readAsDataURL(file);
            reader.onloadend = function() {
                base64Image = reader.result;
                document.getElementById('dropzone-prompt').classList.add('hidden');
                const img = document.getElementById('imagePreview');
                img.src = base64Image;
                img.classList.remove('hidden');
            }
        }

        // Actualización Visual de Estados del Pipeline
        function updateStepStatus(stepId, status, details = "") {
            const step = document.getElementById(stepId);
            const badge = step.querySelector('.step-badge');
            const desc = step.querySelector('.step-desc');
            const iconContainer = step.querySelector('.step-icon');

            // Resetear clases
            step.className = "flex items-start gap-3 p-3 rounded-xl transition ";
            iconContainer.className = "step-icon p-2 rounded-lg ";
            
            if (status === 'active') {
                step.classList.add('bg-indigo-50/70 border border-indigo-100/30');
                badge.className = "step-badge text-[10px] font-bold text-indigo-600 animate-pulse uppercase";
                badge.innerText = "Procesando...";
                iconContainer.classList.add('bg-indigo-600 text-white shadow-sm');
                if (details) desc.innerText = details;
            } else if (status === 'success') {
                step.classList.add('bg-emerald-50/30 border border-emerald-100/20');
                badge.className = "step-badge text-[10px] font-bold text-emerald-600 uppercase";
                badge.innerText = "Completado";
                iconContainer.classList.add('bg-emerald-500 text-white');
                if (details) desc.innerText = details;
            } else if (status === 'failed') {
                step.classList.add('bg-rose-50/40 border border-rose-100/20');
                badge.className = "step-badge text-[10px] font-bold text-rose-600 uppercase";
                badge.innerText = "Error";
                iconContainer.classList.add('bg-rose-500 text-white');
                if (details) desc.innerText = details;
            } else {
                step.classList.add('bg-transparent');
                badge.className = "step-badge text-[10px] text-slate-400 font-semibold uppercase";
                badge.innerText = "Esperando";
                iconContainer.classList.add('bg-slate-100 text-slate-400');
            }
        }

        function setSystemStatus(text, colorClass) {
            const statusEl = document.getElementById('systemStatus');
            statusEl.innerHTML = `<span class="w-1.5 h-1.5 rounded-full ${colorClass}"></span>${text}`;
        }

        // PIPELINE DE AGENTES DE INTELIGENCIA ARTIFICIAL EN ACCIÓN
        async function startAgentPipeline() {
            if (!base64Image) {
                alert("Por favor carga primero una captura de pantalla de la sección.");
                return;
            }

            const brandPrompt = document.getElementById('brandPrompt').value;
            const btnBuild = document.getElementById('btnBuild');
            
            btnBuild.disabled = true;
            btnBuild.classList.add('opacity-65', 'cursor-not-allowed');
            setSystemStatus("Construyendo...", "bg-amber-500 animate-pulse");

            try {
                // --- PASO 1: AGENTE DE VISIÓN ---
                updateStepStatus('step-vision', 'active', 'Analizando layout, textos y paleta de colores...');
                
                const visionResponse = await fetch('/api/analyze-vision', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        image_base64: base64Image,
                        brand_prompt: brandPrompt
                    })
                });

                if (!visionResponse.ok) throw new Error("Fallo en el Agente de Visión.");
                const analysisResult = await visionResponse.json();
                updateStepStatus('step-vision', 'success', `Sección identificada: ${analysisResult.seccion_tipo.toUpperCase()}`);

                // --- PASO 2: AGENTE DE ASSETS ---
                updateStepStatus('step-assets', 'active', 'Pintando y buscando assets mediante Imagen 4.0...');
                const assetsMapping = {};
                
                for (const imgReq of analysisResult.requerimiento_imagenes) {
                    updateStepStatus('step-assets', 'active', `Generando recurso: ${imgReq.id}...`);
                    const assetResponse = await fetch('/api/generate-asset', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            prompt: imgReq.descripcion_para_generar,
                            aspect_ratio: imgReq.aspect_ratio
                        })
                    });
                    if (assetResponse.ok) {
                        const assetData = await assetResponse.json();
                        assetsMapping[imgReq.id] = assetData.secure_url;
                    }
                }
                updateStepStatus('step-assets', 'success', `Se resolvieron ${Object.keys(assetsMapping).length} imágenes de alta gama.`);

                // --- PASO 3: AGENTE DE REDACCIÓN DE CÓDIGO ---
                updateStepStatus('step-code', 'active', 'Maquetando semánticamente en HTML & Tailwind CSS...');
                const codeResponse = await fetch('/api/generate-html-section', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        analysis_json: analysisResult,
                        assets_mapping: assetsMapping
                    })
                });
                
                if (!codeResponse.ok) throw new Error("Fallo en la redacción estética de código.");
                const codeResult = await codeResponse.json();
                updateStepStatus('step-code', 'success', 'Código HTML generado y pulido correctamente.');

                // --- PASO 4: COMPILADOR DOM / FUSIÓN QUIRÚRGICA ---
                updateStepStatus('step-merge', 'active', 'Inyectando componente de forma inteligente...');
                const mergeResponse = await fetch('/api/merge-html', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        new_section_html: codeResult.html_code
                    })
                });

                if (!mergeResponse.ok) throw new Error("Fallo en la fusión DOM.");
                const mergeResult = await mergeResponse.json();
                updateStepStatus('step-merge', 'success', 'Landing page compilada con éxito.');

                // Renderizar Preview consolidada
                renderPreview(mergeResult.total_html);
                setSystemStatus("Listo", "bg-emerald-500");

                // Limpiar zona de carga para la siguiente sección
                base64Image = "";
                document.getElementById('imagePreview').classList.add('hidden');
                document.getElementById('imagePreview').src = "";
                document.getElementById('dropzone-prompt').classList.remove('hidden');

            } catch (err) {
                console.error(err);
                setSystemStatus("Error", "bg-rose-500");
                alert("Ocurrió un error en el pipeline de agentes de IA: " + err.message);
            } finally {
                btnBuild.disabled = false;
                btnBuild.classList.remove('opacity-65', 'cursor-not-allowed');
            }
        }

        // Renderizar el HTML de forma limpia dentro del Iframe virtual
        function renderPreview(html) {
            const iframe = document.getElementById('previewIframe');
            const placeholder = document.getElementById('iframePlaceholder');
            
            if (html) {
                placeholder.classList.add('hidden');
                iframe.classList.remove('hidden');
                
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                doc.open();
                doc.write(html);
                doc.close();
            } else {
                placeholder.classList.remove('hidden');
                iframe.classList.add('hidden');
                iframe.src = "about:blank";
            }
        }

        // Descargar index.html exportable
        async function downloadCode() {
            const response = await fetch('/api/workspace');
            const data = await response.json();
            
            if (!data.html) {
                alert("El lienzo de trabajo está vacío. ¡Genera código primero!");
                return;
            }

            const blob = new Blob([data.html], { type: 'text/html' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'mi-nueva-landing-page.html';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }

        // Reiniciar lienzo de trabajo
        async function resetWorkspace() {
            if (confirm("¿Estás seguro de que quieres limpiar toda tu landing page y empezar de cero?")) {
                await fetch('/api/workspace/reset', { method: 'POST' });
                renderPreview("");
                
                // Resetear monitores
                updateStepStatus('step-vision', 'wait');
                updateStepStatus('step-assets', 'wait');
                updateStepStatus('step-code', 'wait');
                updateStepStatus('step-merge', 'wait');
                setSystemStatus("Ocioso", "bg-slate-400");
            }
        }

        // Al iniciar, cargar estado del backend si ya hay trabajo guardado
        window.addEventListener('load', async () => {
            const response = await fetch('/api/workspace');
            const data = await response.json();
            if (data.html) {
                renderPreview(data.html);
                setSystemStatus("Listo", "bg-emerald-500");
            }
        });
    </script>
</body>
</html>
"""

# --- INICIALIZADOR DE EJECUCIÓN ---

if __name__ == "__main__":
    import uvicorn
    # Inicia el servidor con recarga en caliente para un desarrollo iterativo ágil
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)