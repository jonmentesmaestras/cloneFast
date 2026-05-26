# **Plan de Arquitectura y Desarrollo: Vision-to-Code Landing Page Builder**

Este documento detalla el blueprint técnico y la arquitectura de agentes de Inteligencia Artificial para construir una plataforma SaaS capaz de transformar capturas de pantalla segmentadas en código HTML interactivo, fluido y de alta gama con resolución autónoma de recursos visuales.

## **1\. Arquitectura de Alto Nivel (Tech Stack)**

Para garantizar velocidad de desarrollo, excelente rendimiento de renderizado en tiempo real y facilidad de despliegue, utilizaremos un stack híbrido de Python y TypeScript/JavaScript:

\[ FRONTEND: React / Tailwind \] \<---\> \[ BACKEND: FastAPI (Python) \] \<---\> \[ AGENTES DE IA (Gemini API / LangChain) \]  
              |                                                                     |  
              v                                                                   v  
     \[ Live Preview Iframe \]                       \[ APIs: Imagen / Unsplash / Cloudinary \]

### **Backend (Python)**

* **Framework Principal:** FastAPI por su asincronía nativa, alto rendimiento y autogeneración de OpenAPI.  
* **Orquestación de Agentes:** LangGraph o CrewAI para estructurar los flujos de trabajo basados en agentes interactivos con memoria cíclica.  
* **SDK de IA:** google-genai para el uso avanzado y optimizado de la API de Gemini (Vision, Structured Outputs, Imagen 4.0).

### **Frontend (JavaScript/React)**

* **Estructura:** Aplicación React SPA (Single Page Application).  
* **Visualización:** Split-screen con panel izquierdo para carga de imágenes/prompts e historial, y panel derecho para visualización interactiva del código mediante un \<iframe\> aislado de ejecución.  
* **Editor:** Monaco Editor (el núcleo de VS Code) embebido para edición manual opcional por el usuario.

## **2\. El Motor de Agentes Multi-Agente (Orquestación en Python)**

El proceso secuencial e interactivo que realizamos manualmente se automatiza mediante **cuatro agentes especializados** que colaboran de manera jerárquica:

                           \+--------------------------+  
                           |   AGENTE COORDINADOR     | (Gestión de estado y merge de código)  
                           \+--------------------------+  
                             /          |           \\  
                            v           v            v  
            \+------------------+  \+------------------+  \+-------------------+  
            |  AGENTE VISIÓN   |  |  AGENTE CÓDIGO   |  |   AGENTE ASSETS   |  
            | (Análisis Visual)|  | (Maquetación CSS)|  | (Generación/Stock)|  
            \+------------------+  \+------------------+  \+-------------------+

### **A. Agente de Análisis de Visión (Vision Agent)**

* **Modelo:** gemini-2.5-flash-preview-09-2025 (Análisis multimodal).  
* **Misión:** Recibir la imagen de la sección (captura recortada) y traducirla a una especificación JSON estructurada.  
* **Output Técnico (JSON Schema obligatorio):**  
  {  
    "seccion\_tipo": "hero | cta | caracteristicas | faq | pricing",  
    "paleta\_colores": { "fondo": "hex", "texto\_principal": "hex", "acentos": \["hex"\] },  
    "tipografia": { "estilo": "serif | sans | display", "fuente\_sugerida": "string" },  
    "estructura\_dom": { "columnas\_escritorio": 2, "alineacion": "izquierda | centro" },  
    "texto\_extraido": { "titulo": "string", "subtitulo": "string", "parrafos": \["string"\], "cta\_text": "string" },  
    "requerimiento\_imagenes": \[  
      {  
        "id": "string",  
        "descripcion\_para\_generar": "string\_en\_ingles",  
        "keywords\_stock": \["string"\],  
        "aspect\_ratio": "3:4 | 16:9 | 1:1",  
        "estilo\_visual": "fotografia\_clinica | minimalista\_vectorial | retrato"  
      }  
    \]  
  }

### **B. Agente de Resolución de Assets (Asset Agent)**

Este agente recibe los requerimientos de imágenes de la sección y ejecuta un flujo de decisión inteligente:

1. **Evaluación de Tipo:**  
   * Si requiere un **retrato humano**, realiza una consulta controlada a la API de RandomUser o busca en Unsplash.  
   * Si requiere una **fotografía temática de alta calidad** (ej. "tratamiento estético facial"), consume la API de Unsplash mediante consultas optimizadas en inglés.  
   * Si requiere un **elemento de marketing/producto exclusivo** (ej. "portada de ebook minimalista con hojas doradas"), genera un Prompt técnico avanzado para Imagen 4.0.  
2. **Pipeline de Almacenamiento:** El backend descarga el recurso (o el blob base64 de Imagen) y lo sube de forma asíncrona a un servicio CDN (como Cloudinary o tu propia instancia S3/PostImages) para obtener una URL pública y persistente de alta velocidad.

### **C. Agente de Generación y Fusión de Código (Code Agent)**

* **Modelo:** gemini-2.5-pro (por su capacidad superior de razonamiento de código y seguimiento de instrucciones complejas).  
* **Misión:** Escribir el HTML interactivo utilizando Tailwind CSS embebido, fuentes de Google Fonts e iconos universales de Lucide.  
* **Entradas:** El JSON del *Vision Agent*, las URLs estables del *Asset Agent* y el código HTML de las secciones previamente aprobadas (si existen).  
* **Fusión de Código Estricta (Merge Engine):**  
  * *Si es la primera sección:* Genera el scaffolding completo del documento (\<html\>, \<head\>, \<body\>, header, etc.).  
  * *Si es una sección subsecuente:* Lee el árbol DOM existente, busca el contenedor de secciones principal e inserta la nueva sección de manera semántica antes del pie de página (\<footer\>) o al final del \<body\>. Realiza un merge inteligente de clases personalizadas e inicializa scripts requeridos.

## **3\. Pipeline de Procesamiento de Imágenes (Automatización de URLs)**

Para resolver el problema de las imágenes rotas o locales, el servidor backend implementará un pipeline de almacenamiento intermedio:

\# Backend FastAPI snippet para procesamiento de assets generados por IA  
import httpx  
import cloudinary  
import cloudinary.uploader  
from fastapi import APIRouter, HTTPException

router \= APIRouter()

@router.post("/process-asset")  
async def process\_asset(prompt: str, target\_type: str):  
    try:  
        if target\_type \== "generation":  
            \# 1\. Llamar a Imagen 4.0 usando la librería de Gemini  
            \# (Simulación de llamada a endpoint Gemini/Vertex AI)  
            base64\_img \= await call\_gemini\_imagen\_api(prompt)  
              
            \# 2\. Subir directamente a Cloudinary o S3  
            upload\_result \= cloudinary.uploader.upload(  
                f"data:image/png;base64,{base64\_img}",  
                folder="landing\_builder/assets"  
            )  
            return {"secure\_url": upload\_result\["secure\_url"\]}  
              
        elif target\_type \== "stock":  
            \# Buscar en Unsplash mediante su API oficial  
            photo\_url \= await fetch\_unsplash\_photo(prompt)  
            return {"secure\_url": photo\_url}  
              
    except Exception as e:  
        raise HTTPException(status\_code=500, detail=str(e))

## **4\. El Algoritmo de "Merge" de Secciones (Iterative Compilation)**

Uno de los mayores desafíos en el desarrollo de este SaaS es el **"contexto de código cambiante"**. Si le pides al modelo que reescriba todo el código en cada sección, eventualmente se quedará sin ventana de contexto o cometerá fallos de omisión de código anterior.

Para solucionarlo, implementamos un **compilador de árbol DOM estructurado** en Python:

1. **Scaffolding Base:** El archivo HTML tiene una etiqueta especial de anclaje:  
   \<div id="landing-builder-sections"\>  
       \<\!-- SECTIONS\_PLACEHOLDER \--\>  
   \</div\>

2. **Inserción Quirúrgica:** El backend recibe el código de la nueva sección (únicamente la sección html) y la inserta exactamente en la posición del placeholder utilizando BeautifulSoup4 en Python.  
3. **Gestión de Dependencias:** Si la nueva sección requiere un script específico (ej. un slider o animaciones CSS especiales), el backend inyecta los tags \<script\> o \<style\> al final del documento de forma organizada, asegurando que no haya duplicados.

## **5\. UI/UX: Flujo de Trabajo en la Web App**

Para que la experiencia del usuario sea intuitiva, rápida y fluida:

1. **Paso 1 (Carga):** El usuario arrastra el primer screenshot (Hero) y opcionalmente escribe un prompt con el tono de voz de su marca.  
2. **Paso 2 (Generación Inicial):** En 10 segundos, la pantalla se divide. A la derecha se renderiza la primera sección en el *Live Preview*.  
3. **Paso 3 (Iteración Continuada):** El usuario arrastra el segundo screenshot (ej. CTA de Precios) y hace clic en **"Agregar Sección"**. El sistema analiza, procesa la imagen, genera los assets, actualiza la base de datos de la landing page y renderiza instantáneamente el resultado acumulado.  
4. **Paso 4 (Personalización):** El usuario puede seleccionar bloques de texto de la landing page ya generada en el iframe y pedir modificaciones ("Haz este título más comercial", "Cambia esta imagen por una de un spa minimalista"). El agente de edición actualiza únicamente el nodo seleccionado.  
5. **Paso 5 (Exportación):** Un botón permite descargar el archivo index.html unificado o desplegarlo directamente a un servidor estático (ej. Vercel, Netlify o AWS S3) con un clic.

## **6\. Siguientes Pasos de Implementación**

Para convertir este plan en realidad, te sugiero iniciar con una **Prueba de Concepto (PoC)** minimalista:

* **Hito 1:** Crear la UI en React que permita subir una imagen y enviarla al backend de FastAPI.  
* **Hito 2:** Configurar el backend para llamar a Gemini con instrucciones de análisis visual estructurado (Vision JSON).  
* **Hito 3:** Implementar el procesador de imágenes (Unsplash \+ Imagen 4.0) y almacenamiento en la nube.  
* **Hito 4:** Diseñar el inyector de código HTML de BeautifulSoup en Python.