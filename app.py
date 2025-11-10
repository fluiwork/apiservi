# app.py
import os
import threading
import time
import signal
from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from flask_cors import CORS

# Optional: rate limiter to avoid abusos (instalar flask-limiter)
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    FLASK_LIMITER_AVAILABLE = True
except Exception:
    FLASK_LIMITER_AVAILABLE = False

# ---------------------------
# Config (desde variables de entorno)
# ---------------------------
ALLOWED_ORIGINS_ENV = os.getenv('ALLOWED_ORIGINS', '').strip()
if ALLOWED_ORIGINS_ENV:
    ALLOWED_ORIGINS = [s.strip() for s in ALLOWED_ORIGINS_ENV.split(',') if s.strip()]
else:
    ALLOWED_ORIGINS = ["http://localhost:5000", "http://127.0.0.1:5500"]

MAX_CONCURRENT_SCRAPES = int(os.getenv('MAX_CONCURRENT_SCRAPES', '4'))
PAGE_LOAD_TIMEOUT = int(os.getenv('PAGE_LOAD_TIMEOUT', '30'))
IFRAME_WAIT_SECONDS = int(os.getenv('IFRAME_WAIT_SECONDS', '15'))

# Rate limit (por IP). Ejemplos: "10/minute", "100/hour"
RATE_LIMIT = os.getenv('RATE_LIMIT', '10/minute')

# ---------------------------
# App & CORS
# ---------------------------
app = Flask(__name__)
CORS(app, resources={r"/rastrear": {"origins": ALLOWED_ORIGINS}})

# Si está disponible flask-limiter, inicializarlo. Si no, funcionamos sin él.
if FLASK_LIMITER_AVAILABLE:
    limiter = Limiter(app, key_func=get_remote_address, default_limits=[RATE_LIMIT])
    app.logger.info(f"Limiter activado: {RATE_LIMIT}")
else:
    limiter = None
    app.logger.warning("flask-limiter no está instalado. Recomendado: pip install Flask-Limiter")

# ---------------------------
# Concurrency control (semaphore)
# ---------------------------
scrape_semaphore = threading.BoundedSemaphore(MAX_CONCURRENT_SCRAPES)

# ---------------------------
# Función de scraping (sin estado global, crea driver por petición)
# ---------------------------
def scrape_servientrega(numero_guia):
    """
    Extrae información de rastreo de Servientrega usando Selenium.
    Esta función crea su propio WebDriver y no comparte estado.
    """
    chrome_options = Options()
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--window-size=1920,1080')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--disable-images')
    chrome_options.add_argument('--blink-settings=imagesEnabled=false')
    chrome_options.page_load_strategy = 'eager'

    driver = None
    try:
        # Intentar crear el WebDriver. Capturamos errores tempranos (p.ej. falta chrome/chromedriver).
        try:
            driver = webdriver.Chrome(options=chrome_options)
        except Exception as e_driver_init:
            app.logger.exception("No se pudo iniciar WebDriver")
            return {'success': False, 'error': 'Error al iniciar el navegador en el servidor.'}

        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

        url = f"https://www.servientrega.com/wps/portal/rastreo-envio/detalle?id={numero_guia}&tipo=0"
        app.logger.info(f"🔍 Consultando: {url}")
        driver.get(url)

        app.logger.debug("⏳ Esperando iframe...")
        wait = WebDriverWait(driver, IFRAME_WAIT_SECONDS)
        iframe = wait.until(EC.presence_of_element_located((By.ID, "iframe")))
        app.logger.debug("✓ Iframe encontrado")

        driver.switch_to.frame(iframe)
        wait.until(lambda d: len(d.find_element(By.TAG_NAME, "body").text) > 100)
        app.logger.debug("✓ Contenido cargado")

        js_script = """
        const textoCompleto = document.body.innerText || document.body.textContent || '';
        
        const datos = {
            numeroGuia: null,
            ciudadRecogida: null,
            ciudadDestino: null,
            regimen: null,
            cantidadEnvios: null,
            estado: 'DESCONOCIDO',
            historial: []
        };
        
        const matchNumero = textoCompleto.match(/Número de la guía[\\s\\n]+(\\d+)/i);
        if (matchNumero) datos.numeroGuia = matchNumero[1];
        
        const matchRecogida = textoCompleto.match(/Ciudad de Recogida[\\s\\n]+([^\\n]+)/i);
        if (matchRecogida) datos.ciudadRecogida = matchRecogida[1].trim();
        
        const matchDestino = textoCompleto.match(/Ciudad de Destino[\\s\\n]+([^\\n]+)/i);
        if (matchDestino) datos.ciudadDestino = matchDestino[1].trim();
        
        const matchRegimen = textoCompleto.match(/Régimen[\\s\\n]+([^\\n]+)/i);
        if (matchRegimen) datos.regimen = matchRegimen[1].trim();
        
        const matchCantidad = textoCompleto.match(/Cantidad Envíos?[\\s\\n]+(\\d+)/i);
        if (matchCantidad) datos.cantidadEnvios = matchCantidad[1];
        
        const textoInicio = textoCompleto.substring(0, 300).toUpperCase();
        if (textoInicio.includes('ENTREGADO')) {
            datos.estado = 'ENTREGADO';
        } else if (textoInicio.includes('EN RUTA')) {
            datos.estado = 'EN RUTA';
        } else if (textoInicio.includes('RECIBIDO')) {
            datos.estado = 'RECIBIDO';
        }
        
        const lineas = textoCompleto.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        let enHistorial = false;
        let eventoActual = null;
        
        for (let i = 0; i < lineas.length; i++) {
            const linea = lineas[i];
            
            if (linea.toUpperCase() === 'HISTORIAL') {
                enHistorial = true;
                continue;
            }
            
            if (!enHistorial) continue;
            
            if (/^\\d+$/.test(linea) && parseInt(linea) <= 100) {
                if (eventoActual && eventoActual.descripcion) {
                    datos.historial.push({...eventoActual});
                }
                eventoActual = {
                    numero: parseInt(linea),
                    fecha: null,
                    hora: null,
                    descripcion: null
                };
                continue;
            }
            
            if (!eventoActual) continue;
            
            if (/^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(linea)) {
                eventoActual.fecha = linea;
                continue;
            }
            
            if (/^\\d{2}:\\d{2}(:\\d{2})?$/.test(linea)) {
                eventoActual.hora = linea;
                continue;
            }
            
            if (!eventoActual.descripcion && linea.length > 3) {
                const lineaUpper = linea.toUpperCase();
                if (!lineaUpper.includes('MODIFICAR') && 
                    lineaUpper !== 'HISTORIAL' &&
                    lineaUpper !== 'NUMERO DE LA GUIA' &&
                    lineaUpper !== 'CIUDAD DE RECOGIDA' &&
                    lineaUpper !== 'CIUDAD DE DESTINO') {
                    eventoActual.descripcion = linea;
                }
            }
        }
        
        if (eventoActual && eventoActual.descripcion) {
            datos.historial.push({...eventoActual});
        }
        
        datos.historial.sort((a, b) => a.numero - b.numero);
        
        return datos;
        """

        datos = driver.execute_script(js_script)
        app.logger.info(f"✓ Datos extraídos: Guía {datos.get('numeroGuia', 'N/A')}, {len(datos.get('historial', []))} eventos")

        driver.quit()
        driver = None
        app.logger.debug("✓ Navegador cerrado")

        if not datos or not datos.get('numeroGuia'):
            return {'success': False, 'error': 'No se encontró información para este número de guía.'}

        return {'success': True, 'data': datos}

    except TimeoutException as e:
        app.logger.error(f"❌ Timeout: {str(e)}")
        return {'success': False, 'error': 'Tiempo de espera agotado. Intenta de nuevo.'}
    except WebDriverException as e:
        app.logger.error(f"❌ Error del navegador: {str(e)}")
        return {'success': False, 'error': 'Error al consultar la página.'}
    except Exception as e:
        app.logger.exception(f"❌ Error inesperado: {str(e)}")
        return {'success': False, 'error': f'Error: {str(e)}'}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

# ---------------------------
# Rutas
# ---------------------------
@app.route('/')
def index():
    return render_template('index.html')

# Aplicar limitador si está disponible
if FLASK_LIMITER_AVAILABLE:
    route_decorator = limiter.limit(RATE_LIMIT)
else:
    # decorator "no-op"
    def route_decorator(fn):
        return fn

@app.route('/rastrear', methods=['POST'])
@route_decorator
def rastrear():
    """
    Endpoint que atiende solicitudes síncronas de scraping.
    Controla concurrencia mediante un semáforo para evitar sobrecarga.
    """
    acquired = scrape_semaphore.acquire(blocking=False)
    if not acquired:
        return jsonify({
            'success': False,
            'error': 'Servidor ocupado: demasiadas solicitudes simultáneas. Intenta de nuevo en unos segundos.'
        }), 429

    try:
        data = request.get_json(silent=True) or {}
        numero_guia = (data.get('numero_guia') or '').strip()

        if not numero_guia:
            return jsonify({'success': False, 'error': 'Debes ingresar un número de guía'}), 400

        if not numero_guia.isdigit():
            return jsonify({'success': False, 'error': 'El número de guía debe contener solo dígitos'}), 400

        resultado = scrape_servientrega(numero_guia)
        status_code = 200
        return jsonify(resultado), status_code

    except Exception as e:
        app.logger.exception("Error en /rastrear")
        return jsonify({'success': False, 'error': f'Error en el servidor: {str(e)}'}), 500

    finally:
        # Liberar solo si lo adquirimos
        if acquired:
            try:
                scrape_semaphore.release()
            except Exception:
                app.logger.exception("Error al liberar semaphore")

@app.route('/health', methods=['GET'])
def health():
    """Endpoint simple para monitoreo / readiness."""
    # reportamos el número de "slots" disponibles del semáforo
    # BoundedSemaphore internals no exponen contador públicamente,
    # así que devolvemos la configuración y un mensaje simple.
    return jsonify({
        'status': 'ok',
        'max_concurrent_scrapes': MAX_CONCURRENT_SCRAPES,
        'rate_limit': RATE_LIMIT if FLASK_LIMITER_AVAILABLE else 'not configured',
        'message': 'Healthy'
    }), 200

# ---------------------------
# Graceful shutdown (opcional, para dev)
# ---------------------------
def handle_sigterm(*args):
    app.logger.info("Recibido SIGTERM, finalizando...")
    # aquí podrías intentar limpiar recursos compartidos si los tuvieras
    os._exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

# ---------------------------
# Arranque (solo para desarrollo)
# ---------------------------
if __name__ == '__main__':
    app.logger.info(f"ALLOWED_ORIGINS={ALLOWED_ORIGINS}  MAX_CONCURRENT_SCRAPES={MAX_CONCURRENT_SCRAPES}  RATE_LIMIT={RATE_LIMIT}")
    # En producción usa gunicorn/uwsgi con varios workers. Aquí habilitamos threading para aceptar múltiples conexiones
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
