import os
import uuid
import asyncio
import re
import json
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Page, ElementHandle

# =====================================================================
# DATA MODELS (Pydantic V2 & V1 Compatible)
# =====================================================================

class ElementoFAQ(BaseModel):
    pregunta: str
    respuesta: str

class Testimonial(BaseModel):
    id: str
    nombre: str
    texto: str
    avatar_url: str

class LandingSection(BaseModel):
    id_seccion: str = Field(..., description="ID unico de la seccion (hero, features, faq, etc.)")
    tipo: str = Field(..., description="Tipo de estructura visual")
    color_fondo: str = Field(..., description="Color hexadecimal de fondo computado")
    texto_original: str = Field(..., description="Texto plano crudo dentro de la seccion")
    screenshot_path: str = Field(default="", description="Ruta local de la captura recortada de la seccion")
    faq_items: Optional[List[ElementoFAQ]] = None
    testimonios: Optional[List[Testimonial]] = None
    imagenes_s3: List[str] = Field(default_factory=list, description="Lista de URLs de imagenes originales")
    es_mockup_con_texto: bool = Field(default=False, description="Flag de mockup que requiere traduccion visual")
    es_testimonio_screenshot: bool = Field(default=False, description="Flag que indica si los testimonios son capturas de pantalla/imagenes")

class LandingPageMetadata(BaseModel):
    task_id: str
    url_origen: str
    full_screenshot_path: str
    secciones: List[LandingSection]

# =====================================================================
# TOOL: css_color_extractor
# =====================================================================

class CSSColorExtractor:
    """Utility to convert computed CSS colors to clean HEX values."""
    
    @staticmethod
    def rgb_to_hex(rgb_str: str) -> str:
        """Converts rgb(r,g,b) or rgba(r,g,b,a) to #RRGGBB."""
        rgb_clean = rgb_str.replace(" ", "").lower()
        
        if rgb_clean.startswith("#"):
            return rgb_clean
            
        match = re.match(r"rgba?\((\d+),(\d+),(\d+)(?:,([\d.]+))?\)", rgb_clean)
        if not match:
            if "transparent" in rgb_clean or "rgba(0,0,0,0)" in rgb_clean:
                return "#ffffff"
            return "#ffffff"
            
        r, g, b = map(int, match.groups()[:3])
        
        if len(match.groups()) > 3 and match.group(4) is not None:
            alfa = float(match.group(4))
            if alfa == 0.0:
                return "#ffffff"
        
        return f"#{r:02x}{g:02x}{b:02x}"

# =====================================================================
# MAIN AGENT CLASS: AgenteScraper
# =====================================================================

class AgenteScraper:
    """
    Agent 1: Navigates headlessly, segments layouts, and crawls deeper attributes 
    to retrieve high-res files, while saving full screenshot and cropped segments.
    """
    
    def __init__(self, target_url: str, output_dir: str = "output_scrapes", headless: bool = True):
        self.target_url = target_url
        self.headless = headless
        self.color_extractor = CSSColorExtractor()
        # Create self-contained target task directory
        self.task_id = str(uuid.uuid4())
        self.output_path = os.path.join(output_dir, self.task_id)
        self.assets_path = os.path.join(self.output_path, "assets")
        os.makedirs(self.assets_path, exist_ok=True)

    async def inicializar_y_escanear(self) -> List[LandingSection]:
        """Orchestrates headless browser initialization and segment harvesting with Stealth parameters."""
        async with async_playwright() as p:
            print("[Fase 1] Launching Chromium with STEALTH parameters...")
            
            # Stealth launch configurations to bypass bot-detection blockers natively
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled", # Hides navigator.webdriver
                    "--disable-web-security",
                    "--allow-running-insecure-content",
                    "--no-sandbox",
                    "--disable-setuid-sandbox"
                ]
            )
            
            context = await browser.new_context(
                viewport={"width": 1280, "height": 950},
                device_scale_factor=1,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="en-US,en;q=0.9",
                timezone_id="America/New_York"
            )
            
            page = await context.new_page()
            
            # Stealth script injection to clear webdriver traits
            await page.add_init_script("delete navigator.__proto__.webdriver;")
            
            print(f"[Fase 1] Connecting securely to: {self.target_url}")
            await page.goto(self.target_url, wait_until="networkidle", timeout=90000)
            
            # Natural human-like scrolling and lazy asset loading
            await self._disparar_lazy_loading(page)
            
            # Save raw complete landing page screenshot
            full_screenshot_path = os.path.join(self.output_path, "full_page.png")
            print(f"[Fase 1] Saving full-page layout screenshot...")
            await page.screenshot(full_page=True, path=full_screenshot_path)
            
            # Retrieve structured DOM segment objects
            secciones = await self._escanear_dom(page)
            
            # Build Metadata payload
            metadata = LandingPageMetadata(
                task_id=self.task_id,
                url_origen=self.target_url,
                full_screenshot_path=full_screenshot_path,
                secciones=secciones
            )
            
            # Support both Pydantic V2 (.model_dump()) and Pydantic V1 (.json() fallback)
            if hasattr(metadata, "model_dump"):
                metadata_dict = metadata.model_dump(mode="json")
            else:
                metadata_dict = json.loads(metadata.json())
                
            # Write final metadata back to task folder
            metadata_file_path = os.path.join(self.output_path, "metadata.json")
            with open(metadata_file_path, "w", encoding="utf-8") as f:
                json.dump(metadata_dict, f, indent=2, ensure_ascii=False)
                
            print(f"[Fase 1] Completed layout extraction! Assets & Metadata written cleanly to: {self.output_path}")
            await browser.close()
            return secciones

    async def _disparar_lazy_loading(self, page: Page):
        """Simulates smooth programmatic scroll downs to trigger lazy images."""
        print("[Scraper] Running lazy scroll triggers...")
        try:
            alto_total = await page.evaluate("document.body.scrollHeight")
            paso = 400
            for y in range(0, alto_total, paso):
                await page.evaluate(f"window.scrollTo(0, {y})")
                await page.wait_for_timeout(250)
                if y % 1200 == 0:
                    await self._forzar_carga_de_imagenes_lazy(page)
                    print("    -> OJO SALIENDO DE FORZAR CARGA DE IMAGENES LAZY")
            
            # Scroll back to Top
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(1500)
            await self._forzar_carga_de_imagenes_lazy(page)
        except Exception as e:
            print(f"[Warning] Lazy scroll trigger exception: {e}")

    async def _forzar_carga_de_imagenes_lazy(self, page: Page):
        """
        Natively assists the page's JS image loading without destructive style overriding
        which breaks Swiper/Elementor layout dimensions. Integrates a fast 150ms timeout 
        to prevent blocking when images fail to load due to SSL/connection errors.
        """
        await page.evaluate("""
        async () => {
            const lazyAttributes = ['data-lazy-src', 'data-src', 'data-original', 'lazy-src'];
            const lazySrcsets = ['data-lazy-srcset', 'data-srcset', 'lazy-srcset'];

            document.querySelectorAll('img').forEach(img => {
                for (const attr of lazyAttributes) {
                    const val = img.getAttribute(attr);
                    if (val && !val.startsWith('data:image')) {
                        img.src = val;
                        break;
                    }
                }
                for (const attr of lazySrcsets) {
                    const val = img.getAttribute(attr);
                    if (val) {
                        img.srcset = val;
                        break;
                    }
                }
                img.style.opacity = '1';
                img.style.visibility = 'visible';
                img.classList.remove('lazyload');
                img.classList.add('lazyloaded');
            });

            document.querySelectorAll('div, section').forEach(el => {
                const lazyBg = el.getAttribute('data-bg') || el.getAttribute('data-lazy-bg') || el.getAttribute('data-background');
                if (lazyBg) {
                    el.style.backgroundImage = `url('${lazyBg}')`;
                }
            });

            window.dispatchEvent(new Event('scroll'));
            window.dispatchEvent(new Event('resize'));

            const imgs = Array.from(document.querySelectorAll('img'));
            
            // Timeout race wrapper: guarantees resolution in max 150ms if connection is broken
            const decodePromises = imgs.map(img => {
                return Promise.race([
                    img.decode(),
                    new Promise(resolve => setTimeout(resolve, 150))
                ]).catch(() => {});
            });
            
            await Promise.all(decodePromises);
        }
        """)
        print("[Scraper] FINALIZANDO Lazy load force triggered.")

    async def _obtener_color_fondo_efectivo(self, el: ElementHandle) -> str:
        """Heuristically climbs CSS trees to recover real background colors."""
        try:
            color = await el.evaluate("el => window.getComputedStyle(el).backgroundColor")
            hex_color = self.color_extractor.rgb_to_hex(color)
            
            if hex_color == "#ffffff" or color == "rgba(0,0,0,0)" or color == "transparent":
                parent = await el.evaluate_handle("el => el.parentElement")
                if parent and await parent.as_element():
                    return await self._obtener_color_fondo_efectivo(parent.as_element())
            return hex_color
        except Exception:
            return "#ffffff"

    async def _escanear_dom(self, page: Page) -> List[LandingSection]:
        """Dynamically identifies root structural containers and captures visual crop cuts."""
        print("[Scraper] Executing DOM layout segmentation...")
        
        eval_sections_js = """
        () => {
            // Clean up old identifiers if any exist
            document.querySelectorAll('*[data-pulpo-idx]').forEach(el => el.removeAttribute('data-pulpo-idx'));

            const getElementDetails = (el, i) => {
                el.setAttribute('data-pulpo-idx', i.toString());
                return {
                    idx: i.toString(),
                    id_propuesto: el.getAttribute('id') || el.getAttribute('class')?.split(' ')[0] || `seccion_${i}`
                };
            };

            // Strategy 1: Check for explicit semantic sections
            const semanticSelectors = ["section", "article", ".elementor-section", ".e-con", "div[id*='section']", "div[class*='section']"];
            let candidates = [];
            semanticSelectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    const rect = el.getBoundingClientRect();
                    if (rect.height > 100 && rect.width > 100) {
                        candidates.push(el);
                    }
                });
            });

            // Keep only top-level candidates (no nested semantic elements)
            let rootCandidates = candidates.filter(el => {
                return !candidates.some(other => other !== el && other.contains(el));
            });

            if (rootCandidates.length >= 3) {
                return rootCandidates.map((el, i) => getElementDetails(el, i));
            }

            // Strategy 2: Body Traversal (Fallback for wrappers)
            let current = document.body;
            while (current) {
                const children = Array.from(current.children).filter(child => {
                    const rect = child.getBoundingClientRect();
                    const style = window.getComputedStyle(child);
                    return rect.height > 80 && rect.width > 100 && style.display !== 'none' && style.position !== 'absolute' && style.position !== 'fixed';
                });

                if (children.length === 1) {
                    current = children[0];
                } else if (children.length > 1) {
                    return children.map((el, i) => getElementDetails(el, i));
                } else {
                    break;
                }
            }

            // Ultimate fallback: direct body children
            return Array.from(document.body.children).filter(child => {
                const rect = child.getBoundingClientRect();
                return rect.height > 50;
            }).map((el, i) => getElementDetails(el, i));
        }
        """
        
        lista_secciones_meta = await page.evaluate(eval_sections_js)
        print(f"[Scraper] Analysing {len(lista_secciones_meta)} segmented segments.")
        secciones_detectadas: List[LandingSection] = []

        for meta in lista_secciones_meta:
            try:
                idx_str = meta["idx"]
                id_clean = meta["id_propuesto"]
                
                # Fetch element cleanly via the annotated layout index
                el = await page.query_selector(f"*[data-pulpo-idx='{idx_str}']")
                if not el:
                    continue
                    
                box = await el.bounding_box()
                if not box or box["height"] < 80:
                    continue
                
                # Dynamic Crop Screen Saving
                sec_screenshot_path = os.path.join(self.assets_path, f"seccion_{idx_str}.png")
                try:
                    await el.scroll_into_view_if_needed()
                    await page.wait_for_timeout(300)
                    # Refresh lazy images right before capturing elements
                    await self._forzar_carga_de_imagenes_lazy(page)
                    await page.wait_for_timeout(200)
                    await el.screenshot(path=sec_screenshot_path)
                except Exception as e_snap:
                    print(f"[Warning] Failed to crop capture section {id_clean}: {e_snap}")
                
                hex_fondo = await self._obtener_color_fondo_efectivo(el)
                texto_original = await el.inner_text()
                texto_original_clean = texto_original.strip()
                
                urls_imagenes = await self._ejecutar_escaneo_profundo_imagenes(el)
                
                if len(texto_original_clean) < 10 and not urls_imagenes:
                    continue
                
                tipo_seccion = await self._determinar_tipo_seccion_avanzado(el, texto_original_clean, urls_imagenes)
                es_mockup = await self._clasificar_mockups_avanzado(urls_imagenes)
                
                es_testimonio_screenshot = False
                if tipo_seccion == "testimonios":
                    es_testimonio_screenshot = any(
                        any(x in url.lower() for x in ["depoimento", "testemunho", "testimonio", "whatsapp", "print", "screenshot", "chat", "conversa", "feedback", "comentario", "captura"])
                        for url in urls_imagenes
                    ) or (len(urls_imagenes) > 0 and len(texto_original_clean) < 200)

                faq_items = await self._intentar_extraer_faqs(el, tipo_seccion)
                testimonios = await self._intentar_extraer_testimonios_avanzados(el, tipo_seccion, urls_imagenes, es_testimonio_screenshot)
                
                # Strip the stamped attribute to keep DOM clean
                await el.evaluate("(el) => el.removeAttribute('data-pulpo-idx')")

                secciones_detectadas.append(LandingSection(
                    id_seccion=id_clean,
                    tipo=tipo_seccion,
                    color_fondo=hex_fondo,
                    texto_original=texto_original_clean,
                    screenshot_path=sec_screenshot_path,
                    faq_items=faq_items,
                    testimonios=testimonios,
                    imagenes_s3=urls_imagenes,
                    es_mockup_con_texto=es_mockup,
                    es_testimonio_screenshot=es_testimonio_screenshot
                ))
            except Exception as e:
                print(f"[Warning] Segment parse fault on index {meta.get('idx', '?')}: {e}")
                continue
                
        return secciones_detectadas

    async def _ejecutar_escaneo_profundo_imagenes(self, el: ElementHandle) -> List[str]:
        """Deep Attribute Scan: Extracts hidden assets and clean S3 links."""
        js_image_harvester = """
        (container) => {
            const extracted = new Set();
            const elements = container.querySelectorAll('*');
            const imgRegex = /https?:\\/\\/[^\\s"'><\\)]+\\.(?:jpg|jpeg|png|webp|gif|svg)/gi;
            
            function parseStringAndAddUrls(str) {
                if (!str || typeof str !== 'string') return;
                const matches = str.match(imgRegex);
                if (matches) {
                    matches.forEach(url => {
                        const cleanUrl = url.split('?')[0].split('&')[0];
                        if (!cleanUrl.includes('data:image') && !cleanUrl.includes('placeholder')) {
                            extracted.add(cleanUrl);
                        }
                    });
                }
                if (str.includes('%3A') || str.includes('%2F') || str.startsWith('#elementor-action')) {
                    try {
                        const decoded = decodeURIComponent(str);
                        const subMatches = decoded.match(imgRegex);
                        if (subMatches) subMatches.forEach(u => extracted.add(u.split('?')[0]));
                    } catch(e) {}
                }
            }

            elements.forEach(el => {
                if (el.tagName === 'IMG') {
                    parseStringAndAddUrls(el.src);
                    parseStringAndAddUrls(el.getAttribute('data-lazy-src'));
                    parseStringAndAddUrls(el.getAttribute('data-src'));
                    parseStringAndAddUrls(el.getAttribute('srcset'));
                    parseStringAndAddUrls(el.getAttribute('data-lazy-srcset'));
                    parseStringAndAddUrls(el.getAttribute('data-srcset'));
                }
                if (el.tagName === 'SOURCE') {
                    parseStringAndAddUrls(el.getAttribute('srcset'));
                    parseStringAndAddUrls(el.getAttribute('data-lazy-srcset'));
                }
                if (el.tagName === 'A') {
                    parseStringAndAddUrls(el.getAttribute('href'));
                }
                for (let i = 0; i < el.attributes.length; i++) {
                    const attr = el.attributes[i];
                    parseStringAndAddUrls(attr.value);
                }
            });
            
            const bg = window.getComputedStyle(container).backgroundImage;
            if (bg && bg !== 'none') {
                const matches = bg.match(/url\\(['"]?([^'"]+)['"]?\\)/i);
                if (matches && matches[1].startsWith('http')) {
                    extracted.add(matches[1].split('?')[0]);
                }
            }
            
            return Array.from(extracted);
        }
        """
        try:
            return await el.evaluate(js_image_harvester)
        except Exception as err:
            print(f"[Error] Failed to harvest images deep: {err}")
            return []

    async def _determinar_tipo_seccion_avanzado(self, el: ElementHandle, texto: str, urls_imagenes: List[str]) -> str:
        """Predicts structural type using both keyword matches and asset strings."""
        texto_lower = texto.lower()
        
        keywords_testimonios = [
            "depoimento", "opiniones", "testigos", "testemunho", "testimonios", "depoimentos", 
            "reviews", "clientes", "comentarios", "o que dizem", "lo que dicen", "resultados",
            "quem já utilizou", "quem ja utilizou"
        ]
        
        if any(x in texto_lower for x in keywords_testimonios):
            return "testimonios"
            
        clase_seccion = (await el.get_attribute("class") or "").lower()
        id_seccion = (await el.get_attribute("id") or "").lower()
        
        if any(x in clase_seccion or x in id_seccion for x in ["carousel", "carrossel", "slider", "glide", "slick", "swiper", "depoimento", "testimonial"]):
            return "testimonios"
            
        for url in urls_imagenes:
            url_lower = url.lower()
            if any(x in url_lower for x in ["depoimento", "testemunho", "whatsapp", "screenshot", "chat", "captura", "print"]):
                return "testimonios"
                
        faqs = await el.query_selector_all("details, .faq-item, .accordion")
        if faqs or any(x in texto_lower for x in ["perguntas", "preguntas", "faq", "frecuentes"]):
            return "faq"
            
        columnas = await el.query_selector_all("[class*='grid'], [class*='col-'], .elementor-row")
        if len(columnas) > 1:
            return "grid"
            
        return "text_only"

    async def _clasificar_mockups_avanzado(self, urls_imagenes: List[str]) -> bool:
        """Validates if visual textures indicate presence of infoproduct covers."""
        for url in urls_imagenes:
            url_lower = url.lower()
            if any(k in url_lower for k in ["mockup", "ebook", "capa", "livro", "book", "product", "bono", "bonus", "garantia", "seal"]):
                return True
        return False

    async def _intentar_extraer_faqs(self, el: ElementHandle, tipo: str) -> Optional[List[ElementoFAQ]]:
        """Parses FAQ blocks cleanly."""
        if tipo != "faq":
            return None
            
        faq_list = []
        details_elements = await el.query_selector_all("details")
        for d in details_elements:
            try:
                summary = await d.query_selector("summary")
                pregunta = await summary.inner_text() if summary else "Pregunta"
                respuesta = await d.evaluate("el => { const clone = el.cloneNode(true); const sum = clone.querySelector('summary'); if(sum) sum.remove(); return clone.innerText; }")
                faq_list.append(ElementoFAQ(pregunta=pregunta.strip(), respuesta=respuesta.strip()))
            except Exception:
                continue
            
        return faq_list if faq_list else None

    async def _intentar_extraer_testimonios_avanzados(self, el: ElementHandle, tipo: str, urls_imagenes: List[str], es_screenshot: bool) -> Optional[List[Testimonial]]:
        """Parses reviews into structured models."""
        if tipo != "testimonios":
            return None
            
        testimonios_detectados = []
        
        if es_screenshot and urls_imagenes:
            urls_capturas = [
                u for u in urls_imagenes 
                if any(x in u.lower() for x in ["whatsapp", "screenshot", "captura", "print", "depoimento", "chat", "conversa"])
            ]
            
            if not urls_capturas:
                urls_capturas = urls_imagenes
                
            for i, img_url in enumerate(urls_capturas[:10]): # Limitar a 10 testimoniales
                testimonios_detectados.append(Testimonial(
                    id=f"test_screenshot_{i}",
                    nombre=f"Captura de Testimonio {i+1}",
                    texto="[Captura de WhatsApp / Testimonio de Cliente]",
                    avatar_url=img_url
                ))
            return testimonios_detectados
            
        tarjetas = await el.query_selector_all("[class*='testimonial'], [class*='depoimento'], .card")
        for i, tarjeta in enumerate(tarjetas[:6]):
            try:
                texto_tarjeta = await tarjeta.inner_text()
                img_avatar = await tarjeta.query_selector("img")
                avatar_url = ""
                if img_avatar:
                    avatar_url = await img_avatar.get_attribute("src") or ""
                    
                lineas = [l.strip() for l in texto_tarjeta.split("\n") if l.strip()]
                if len(lineas) >= 2:
                    nombre = lineas[-1] if len(lineas[-1]) < 30 else lineas[0]
                    texto_test = " ".join([l for l in lineas if l != nombre])
                    
                    testimonios_detectados.append(Testimonial(
                        id=f"test_{i}",
                        nombre=nombre,
                        texto=texto_test,
                        avatar_url=avatar_url
                    ))
            except Exception:
                continue
                
        return testimonios_detectados if testimonios_detectados else None

# =====================================================================
# FULL CLONE ORCHESTRATOR
# =====================================================================

async def clonar_landing_completa(
    url: str,
    bucket: str,
    folder: str | None = None,
    headless: bool = False,
) -> str:
    """Scrape a landing page URL and clone it section-by-section via Gemini + OpenAI.

    Phase A — Playwright scrapes the page and saves per-section PNG crops.
    Phase B — Each crop is fed sequentially through the Gemini/OpenAI pipeline.
              Images are uploaded to s3://{bucket}/{folder}/images/ at generation time.
    Phase C — Assembled HTML is written locally as index.html and deployed to
              s3://{bucket}/{folder}/index.html.

    Args:
        url:      Landing page to clone.
        bucket:   Target S3 bucket name.
        folder:   Key prefix / subfolder inside the bucket (e.g. "pasta").
                  Defaults to the scraper's task UUID so every run is isolated.
        headless: Run Playwright headlessly (default False for visibility).

    Returns:
        Versioned public URL:
        https://{bucket}.s3.us-east-1.amazonaws.com/{folder}/index.html?v={timestamp}

    CLI usage:
        python -m backend.scraper_del_agente_de_escaneo <url> --bucket my-bucket [--folder pasta] [--headless]
    """
    # Lazy import keeps the scraper usable standalone without backend installed
    from backend.main import create_session_state, process_section, _executor
    from backend.s3 import upload_html

    # ── Phase A: scrape ───────────────────────────────────────────────────
    print(f"\n[Clone] ═══ FASE 1/3 — ESCANEO ═══")
    print(f"[Clone] URL: {url}")
    agente = AgenteScraper(url, headless=headless)
    secciones = await agente.inicializar_y_escanear()
    print(f"[Clone] Scraper produjo {len(secciones)} secciones")

    # Resolve folder: default to scraper's task UUID for isolation
    resolved_folder = folder if folder else agente.task_id
    print(f"[Clone] Bucket: {bucket}  |  Folder: {resolved_folder}")

    # ── Phase B: clone sequentially (GeminiSession is stateful) ──────────
    print(f"\n[Clone] ═══ FASE 2/3 — CLONADO ═══")
    session = create_session_state(s3_bucket=bucket, s3_folder=resolved_folder)

    for i, sec in enumerate(secciones, 1):
        if not sec.screenshot_path or not os.path.exists(sec.screenshot_path):
            print(f"[Clone] ⚠ Sección {i} ({sec.id_seccion}) — sin screenshot, saltando")
            continue

        with open(sec.screenshot_path, "rb") as f:
            image_bytes = f.read()

        if not image_bytes:
            print(f"[Clone] ⚠ Sección {i} ({sec.id_seccion}) — screenshot vacío, saltando")
            continue

        print(f"[Clone] Sección {i}/{len(secciones)} — id={sec.id_seccion} | tipo={sec.tipo}")
        try:
            result = await process_section(session, image_bytes, brand_prompt="", executor=_executor)
            print(f"[Clone]   ✓ +{result['new_images']} imágenes | {result['html_bytes']} bytes acumulados")
            if result["unfilled_placeholders"]:
                print(f"[Clone]   ⚠ Placeholders sin rellenar: {result['unfilled_placeholders']}")
        except RuntimeError as e:
            print(f"[Clone]   ✗ Error en sección {i}: {e} — continuando con la siguiente")
            continue

    # ── Phase C: save locally + deploy to S3 ─────────────────────────────
    print(f"\n[Clone] ═══ FASE 3/3 — DEPLOY ═══")

    # Local copy as index.html (useful for inspection/debugging)
    output_html_path = os.path.join(agente.output_path, "index.html")
    with open(output_html_path, "w", encoding="utf-8") as f:
        f.write(session["html"])
    abs_path = os.path.abspath(output_html_path)
    print(f"[Clone] HTML local: {abs_path}")

    # Upload to S3 and get versioned public URL
    public_url = upload_html(session["html"], bucket=bucket, folder=resolved_folder)

    print(f"\n[Clone] ══════════════════════════════════════════")
    print(f"[Clone] ✓ COMPLETADO")
    print(f"[Clone] Secciones clonadas  : {session['section_count']}")
    print(f"[Clone] Imágenes generadas  : {session['image_count']}")
    print(f"[Clone] HTML local          : {abs_path}")
    print(f"[Clone] URL pública         : {public_url}")
    print(f"[Clone] ══════════════════════════════════════════")
    return public_url


# =====================================================================
# CLI ENTRYPOINT
# =====================================================================

async def main():
    import sys

    args = sys.argv[1:]

    # ── Flags ─────────────────────────────────────────────────────────────
    headless = "--headless" in args

    def _flag(name: str) -> str | None:
        """Return the value after --name, or None if not present."""
        for i, a in enumerate(args):
            if a == name and i + 1 < len(args):
                return args[i + 1]
            if a.startswith(f"{name}="):
                return a.split("=", 1)[1]
        return None

    bucket = _flag("--bucket")
    folder = _flag("--folder")  # optional — defaults to task UUID inside the orchestrator

    # ── Positional: URL ───────────────────────────────────────────────────
    positional = [a for a in args if not a.startswith("--")]
    url = positional[0] if positional else "https://guiaspracticaspro.online/20protocolos-4/"

    # ── Validate ──────────────────────────────────────────────────────────
    if not bucket:
        print("[Error] --bucket is required.")
        print("Usage: python -m backend.scraper_del_agente_de_escaneo <url> --bucket <bucket-name> [--folder <subfolder>] [--headless]")
        sys.exit(1)

    await clonar_landing_completa(url, bucket=bucket, folder=folder, headless=headless)

if __name__ == "__main__":
    asyncio.run(main())