"""
Simple Llama - Orquestador Local de Modelos para llama.cpp
Puerto de escucha: 11435 (Compatible con OpenAI, llama.cpp y Ollama)
"""

import sys
import os
import time
import json
import re
import urllib.request
import urllib.error
import threading
from socketserver import ThreadingMixIn
from http.server import HTTPServer, BaseHTTPRequestHandler

# Asegurar codificación UTF-8 en consola de Windows
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from config import (
    BASE_DIR,
    PROXY_HOST,
    PROXY_PORT,
    BACKEND_HOST,
    BACKEND_PORT,
    MODELS,
    DEFAULT_MODEL,
    reload_models,
    load_settings,
    save_settings,
    is_lan_allowed,
    get_local_ip,
    get_idle_timeout_minutes,
    set_idle_timeout,
    get_model_details,
    save_model_profile,
    create_model_profile,
    delete_model_profile,
    scan_all_available_gguf,
)
from manager import manager


def set_console_title(title):
    """Actualiza la barra de título de la consola de Windows / Terminal en tiempo real."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def sync_console_status(event_msg=None):
    """Sincroniza la barra de título de la consola y emite un log de estado actualizado en CMD."""
    model_name = manager.current_model or "En reposo (0 MB VRAM)"
    lan_on = is_lan_allowed()
    local_ip = get_local_ip()
    lan_str = f"LAN: ON ({local_ip})" if lan_on else "LAN: OFF"
    t_mins = get_idle_timeout_minutes()
    timeout_str = f"Reposo: {t_mins}m" if t_mins > 0 else "Reposo: OFF"

    set_console_title(f"Simple Llama • {model_name} • {lan_str} • {timeout_str}")

    if event_msg:
        print(f"\n[Consola Actualizada] >>> {event_msg}")
        print(f"  • Modelo activo:   {model_name}")
        print(f"  • Red Wi-Fi/LAN:   {'ON (http://' + local_ip + ':' + str(PROXY_PORT) + ')' if lan_on else 'OFF (Solo Localhost)'}")
        print(f"  • Auto-apagado:    {str(t_mins) + ' min' if t_mins > 0 else 'Desactivado (Nunca)'}")
        print("-" * 64)


def trigger_background_sync():
    """Dispara la sincronización automática de OpenCode y Pi Agent en segundo plano sin bloquear el servidor."""
    def _worker():
        try:
            from sync_opencode import sync_opencode, sync_pi
            updated = reload_models()
            sync_opencode(updated)
            sync_pi(updated)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


# Rutas que representan prompts reales (solo estas deben despertar la GPU automáticamente)
INFERENCE_PATHS = {
    "/v1/chat/completions",
    "/v1/completions",
    "/chat/completions",
    "/completions",
    "/api/chat",
    "/api/generate",
    "/completion",
}

def sanitize_messages(messages, default_system_prompt=None):
    """
    Sanitiza la lista de mensajes para compatibilidad universal con Jinja y motores locales:
    1. Si no hay mensaje de sistema y hay un default, se inyecta en la posición 0.
    2. Si hay múltiples mensajes 'system' consecutivos al inicio, los une en uno solo.
    3. Si un cliente (como Pi Agent) envía mensajes 'system' en medio de la conversación
       (después de un 'user' o 'assistant'), se convierten a rol 'user' con prefijo [System Context]:
       evitando la excepción de Jinja: "System message must be at the beginning".
    """
    if not isinstance(messages, list) or not messages:
        if default_system_prompt:
            return [{"role": "system", "content": default_system_prompt}]
        return messages

    has_system = any(isinstance(m, dict) and m.get("role") == "system" for m in messages)
    if not has_system and default_system_prompt:
        messages = [{"role": "system", "content": default_system_prompt}] + list(messages)

    sanitized = []
    first_system_kept = False
    conv_started = False

    for msg in messages:
        if not isinstance(msg, dict):
            sanitized.append(msg)
            continue

        role = msg.get("role")
        if role == "system":
            if not conv_started and not first_system_kept:
                first_system_kept = True
                sanitized.append(dict(msg))
            elif not conv_started and first_system_kept:
                prev = sanitized[0]
                prev_c = prev.get("content", "")
                curr_c = msg.get("content", "")
                if isinstance(prev_c, str) and isinstance(curr_c, str):
                    prev["content"] = f"{prev_c}\n\n{curr_c}" if prev_c else curr_c
                else:
                    new_m = dict(msg)
                    new_m["role"] = "user"
                    sanitized.append(new_m)
            else:
                new_m = dict(msg)
                new_m["role"] = "user"
                c = new_m.get("content", "")
                if isinstance(c, str):
                    new_m["content"] = f"[System Context]: {c}" if c else "[System Context]"
                elif isinstance(c, list):
                    new_content = []
                    prefixed = False
                    for item in c:
                        if isinstance(item, dict) and item.get("type") == "text" and not prefixed:
                            it = dict(item)
                            it["text"] = f"[System Context]: {item.get('text', '')}"
                            new_content.append(it)
                            prefixed = True
                        else:
                            new_content.append(item)
                    new_m["content"] = new_content
                sanitized.append(new_m)
        else:
            conv_started = True
            sanitized.append(msg)

    return sanitized


def render_dashboard():
    """Genera el Dashboard HTML auto-llenado dinámicamente según config.py"""
    reload_models()
    template_path = os.path.join(BASE_DIR, "templates", "dashboard.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return "<h1>Error: No se encontró templates/dashboard.html</h1>"

    status_label = manager.current_model or "Ninguno (0 MB VRAM)"
    is_active = manager.current_model is not None

    if is_active:
        action_buttons = (
            '<div class="status-indicator active"><span class="dot-ping"></span> GPU ACTIVA</div> '
            f'<a href="http://localhost:{BACKEND_PORT}" target="_blank" class="btn-chat">Abrir Chat Web &#8599;</a> '
            '<form method="POST" action="/unload" style="display:inline-block; margin-left:8px; margin-bottom:0;">'
            '<button type="submit" class="btn-danger">Liberar GPU</button>'
            '</form>'
        )
    else:
        action_buttons = '<div class="status-indicator idle"><span class="dot-ping"></span> EN REPOSO</div>'

    # Selector dinámico de watchdog timeout
    current_timeout = get_idle_timeout_minutes()
    timeout_options = [
        (10, "10 min (Recomendado)"),
        (30, "30 min"),
        (60, "60 min"),
        (0, "Nunca / OFF"),
    ]
    opts_html = []
    for val, lbl in timeout_options:
        sel = "selected" if val == current_timeout else ""
        opts_html.append(f'<option value="{val}" {sel}>{lbl}</option>')

    timeout_selector_html = f'''
    <div class="timeout-wrap" title="Auto-apagado del servidor llama.cpp por inactividad">
      <span class="timeout-icon">⏱</span>
      <select id="timeoutSelect" class="select-timeout" onchange="changeIdleTimeout(this.value)">
        {''.join(opts_html)}
      </select>
    </div>
    '''

    # Agrupar modelos por familia dinámicamente
    families = {}
    for m_id, cfg in MODELS.items():
        fam = cfg.get("family", "Otros Modelos")
        if fam not in families:
            families[fam] = []
        families[fam].append((m_id, cfg))

    sections_html = []
    for fam_name, models_in_fam in families.items():
        cards_html = []
        for m_id, cfg in models_in_fam:
            badge_color = cfg.get("badge_color", "#38bdf8")
            badge_text = cfg.get("badge", f"{cfg['ctx']} tokens")
            file_exists = cfg.get("file_exists", True)
            if not file_exists:
                status_warn = '<span class="badge" style="background:rgba(239, 68, 68, 0.15); color:#f87171; border:1px solid rgba(239, 68, 68, 0.3);">⚠️ Archivo no encontrado</span>'
                btn_attr = 'disabled style="opacity:0.4; cursor:not-allowed;"'
                btn_text_display = "Archivo Faltante"
            elif is_active and m_id == manager.current_model:
                status_warn = f'<span class="badge" style="background:{badge_color}14; color:{badge_color}; border:1px solid {badge_color}33;">{badge_text}</span>'
                btn_attr = 'style="background: rgba(16, 185, 129, 0.18); border-color: #10b981; color: #10b981; font-weight:600;"'
                btn_text_display = "⚡ Reiniciar en GPU"
            else:
                btn_text = "Lanzar con Visión" if cfg.get("vision") else "Lanzar en GPU"
                btn_bg = "background: rgba(147, 51, 234, 0.12); border-color: rgba(147, 51, 234, 0.35); color: #c084fc;" if cfg.get("vision") else ""
                status_warn = f'<span class="badge" style="background:{badge_color}14; color:{badge_color}; border:1px solid {badge_color}33;">{badge_text}</span>'
                btn_attr = f'style="{btn_bg}"'
                btn_text_display = btn_text

            cards_html.append(f"""
            <div class="card">
              <div class="card-top">
                <div class="card-header">
                  <h3>{m_id}</h3>
                  {status_warn}
                </div>
                <p>{cfg['desc']}</p>
              </div>
              <div class="card-bottom">
                <form method="POST" action="/load" style="margin:0; flex:1;">
                  <input type="hidden" name="model" value="{m_id}">
                  <button type="submit" class="btn" {btn_attr}>{btn_text_display}</button>
                </form>
                <button type="button" class="btn-config" onclick="openEditModal('{m_id}')" title="Configurar parámetros de {m_id}">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="3"></circle>
                    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
                  </svg>
                </button>
              </div>
            </div>
            """)

        sections_html.append(f"""
        <div class="family-section">
          <div class="family-header">
            <h2 class="family-title">{fam_name}</h2>
          </div>
          <div class="grid">
            {''.join(cards_html)}
          </div>
        </div>
        """)

    if not sections_html:
        catalog = scan_all_available_gguf()
        all_found = catalog.get("all", [])
        if not all_found:
            sections_html.append(f"""
            <div style="text-align:center; padding:48px 24px; background:var(--surface-1); border:1px dashed var(--border-subtle); border-radius:14px; margin-bottom:36px;">
              <div style="font-size:40px; margin-bottom:12px;">📦</div>
              <h3 style="font-size:16px; font-weight:600; margin:0 0 8px 0; color:#fff;">No se encontraron modelos en la carpeta models/</h3>
              <p style="font-size:13px; color:var(--text-muted); max-width:540px; margin:0 auto; line-height:1.5;">
                Coloca tus archivos <code>.gguf</code> dentro de la carpeta <code>orchestrator/models/</code> para comenzar a usarlos.
              </p>
            </div>
            """)
        else:
            sections_html.append(f"""
            <div style="text-align:center; padding:48px 24px; background:var(--surface-1); border:1px dashed rgba(56, 189, 248, 0.35); border-radius:14px; margin-bottom:36px;">
              <div style="font-size:40px; margin-bottom:12px;">⚡</div>
              <h3 style="font-size:16px; font-weight:600; margin:0 0 8px 0; color:#fff;">¡Se detectaron {len(all_found)} archivo(s) .gguf en models/!</h3>
              <p style="font-size:13px; color:var(--text-muted); max-width:540px; margin:0 auto 18px auto; line-height:1.5;">
                Tienes modelos listos en disco pero aún no has registrado un perfil en <code>config.json</code>. Haz clic abajo para crear tu primer perfil y asignarlo a una familia.
              </p>
              <button type="button" class="btn-create-global" onclick="openCreateModal()">+ Crear Perfil para tu Modelo</button>
            </div>
            """)

    lan_on = is_lan_allowed()
    local_ip = get_local_ip()
    if lan_on:
        lan_toggle_html = f'''<form method="POST" action="/toggle-lan" style="display:inline;">
          <button type="submit" class="btn-toggle btn-toggle-on" title="Acceso Wi-Fi activo ({local_ip}). Clic para desactivar">
            <span class="dot-ping"></span> Red Local: ON ({local_ip})
          </button>
        </form>'''
    else:
        lan_toggle_html = '''<form method="POST" action="/toggle-lan" style="display:inline;">
          <button type="submit" class="btn-toggle btn-toggle-off" title="Clic para permitir acceso desde celular/tablet en Wi-Fi">
            <span class="dot-ping"></span> Red Local: OFF
          </button>
        </form>'''

    html = html.replace("{{STATUS_LABEL}}", status_label)
    html = html.replace("{{LAN_TOGGLE}}", lan_toggle_html)
    html = html.replace("{{TIMEOUT_SELECTOR}}", timeout_selector_html)
    html = html.replace("{{ACTION_BUTTONS}}", action_buttons)
    html = html.replace("{{MODEL_SECTIONS}}", "\n".join(sections_html))
    html = html.replace("{{PROXY_PORT}}", str(PROXY_PORT))
    return html


# --- SERVIDOR HTTP MULTIHILO Y SILENCIOSO (EVITA BLOQUEOS EN STREAMING) ---
class QuietHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        super().handle_error(request, client_address)


class ProxyHandler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        # Silenciar logs verbosos internos de HTTP
        pass

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _is_loopback_client(self):
        client_ip = self.client_address[0]
        return client_ip in ("127.0.0.1", "::1", "localhost")

    def _check_access(self):
        """Verifica si el cliente tiene permiso de conexión según la configuración de Red Local"""
        if self._is_loopback_client() or is_lan_allowed():
            return True
        self.send_response(403)
        self._send_cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'{"error": "Acceso por red local desactivado (OFF). Activalo en el panel web desde la PC."}')
        return False

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        if not self._check_access():
            return

        # Ignorar peticiones de precarga especulativa de navegadores (Prefetch / Prerender)
        purpose = self.headers.get("Purpose", "") or self.headers.get("Sec-Purpose", "")
        if "prefetch" in purpose.lower() or "prerender" in purpose.lower():
            self.send_response(204)
            self.end_headers()
            return

        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # 1. Lista de modelos (Estándar OpenAI y llama.cpp para Zed / OpenCode)
        if self.path in ["/v1/models", "/models"]:
            reload_models()
            manager.touch()
            model_list = [
                {
                    "id": m_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "simple-llama",
                    "permission": [],
                    "root": m_id,
                    "parent": None,
                    "max_tokens": cfg["ctx"],
                    "context_window": cfg["ctx"],
                    "context_length": cfg["ctx"],
                    "capabilities": {
                        "images": cfg.get("vision", False),
                        "tools": True,
                    },
                }
                for m_id, cfg in MODELS.items()
            ]
            res_body = json.dumps({"object": "list", "data": model_list}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors()
            self.end_headers()
            self.wfile.write(res_body)
            return

        # 2. Lista de modelos para clientes formato Ollama (/api/tags)
        if self.path == "/api/tags":
            reload_models()
            manager.touch()
            models_data = [
                {
                    "name": m_id,
                    "model": m_id,
                    "modified_at": "2026-10-08T00:00:00Z",
                    "size": 4215695776,
                    "digest": "sha256-df0fd4ee0707...",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": cfg.get("family", "gemma"),
                        "families": [cfg.get("family", "gemma"), "clip"] if cfg.get("vision", False) else [cfg.get("family", "gemma")],
                        "parameter_size": "7.5B",
                        "quantization_level": "Q4_0"
                    }
                }
                for m_id, cfg in MODELS.items()
            ]
            res_body = json.dumps({"models": models_data}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors()
            self.end_headers()
            self.wfile.write(res_body)
            return

        # 3. Health check
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self._send_cors()
            self.end_headers()
            self.wfile.write(b"OK - Mini-Orquestador Activo")
            return

        # 4. El puerto 8080 es SIEMPRE el Dashboard
        if self.path in ["/", "/dashboard"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_cors()
            self.end_headers()
            self.wfile.write(render_dashboard().encode("utf-8"))
            return

        # 5. Endpoint /props (Auto-descubrimiento exacto de contexto para Zed)
        if self.path.startswith("/props"):
            active_name = manager.current_model or DEFAULT_MODEL
            active_cfg = MODELS.get(active_name, MODELS[DEFAULT_MODEL])
            active_ctx = active_cfg["ctx"]
            props_data = {
                "default_generation_settings": {
                    "n_ctx": active_ctx,
                    "params": {"n_predict": -1, "max_tokens": active_ctx}
                },
                "total_slots": 1,
                "model_alias": active_name,
                "chat_template_caps": {
                    "supports_tools": True,
                    "supports_images": active_cfg.get("vision", False),
                }
            }
            res_body = json.dumps(props_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors()
            self.end_headers()
            self.wfile.write(res_body)
            return

        # 6. API: Detalles de un modelo para el editor web
        if self.path.startswith("/api/model/details"):
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            m_id = q.get("model", [None])[0]
            details = get_model_details(m_id) if m_id else None
            if details:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps(details).encode("utf-8"))
            else:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(b'{"error": "Modelo no encontrado"}')
            return

        # 7. API: Catálogo completo de archivos .gguf para los modales
        if self.path == "/api/catalog":
            catalog = scan_all_available_gguf()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors()
            self.end_headers()
            self.wfile.write(json.dumps(catalog).encode("utf-8"))
            return

        # 8. API: Consultar si hubo un error al arrancar el último modelo
        if self.path == "/api/last-error":
            err = manager.get_last_error()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_cors()
            self.end_headers()
            self.wfile.write(json.dumps({"error": err}).encode("utf-8"))
            return

        # Si el modelo está corriendo y piden un recurso estático (slots, metrics, etc.), reenviar al backend
        if manager.current_model is not None:
            self._proxy_request("GET")
        else:
            # Si piden APIs en reposo no contempladas arriba, responder JSON neutro
            if self.path.startswith("/slots") or self.path.startswith("/api"):
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self._send_cors()
                self.end_headers()
                self.wfile.write(b'{"error": "Modelo en reposo (GPU libre)"}')
                return
            # Si no hay modelo y piden otra página, devolver al Dashboard
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

    def do_POST(self):
        # 0. Alternar acceso a red local (Wi-Fi/LAN)
        if self.path == "/toggle-lan":
            if self._is_loopback_client():
                cfg = load_settings()
                cfg["lan_access"] = not cfg.get("lan_access", False)
                save_settings(cfg)
                status_str = "ACTIVADO (ON)" if cfg["lan_access"] else "DESACTIVADO (OFF)"
                sync_console_status(f"Red Local Wi-Fi/LAN: {status_str}")
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        if not self._check_access():
            return

        # 1. Cargar modelo explícitamente desde el panel web
        if self.path == "/load":
            content_length = int(self.headers.get("Content-Length", 0))
            body_str = self.rfile.read(content_length).decode("utf-8", errors="ignore") if content_length > 0 else ""
            import urllib.parse
            params = urllib.parse.parse_qs(body_str)
            target = params.get("model", [DEFAULT_MODEL])[0]
            print(f"\n[Panel Web] Clic manual del usuario -> Cargando '{target}' en GPU...")
            ok = manager.ensure_model(target, force_reload=True)
            if ok:
                sync_console_status(f"Modelo cargado: '{target}'")
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # 2. Descargar modelo / Liberar GPU desde el panel web
        if self.path == "/unload":
            print("\n[Panel Web] Clic manual del usuario -> Liberando GPU y RAM...")
            manager.stop_server()
            sync_console_status("GPU y memoria liberadas (0 MB VRAM)")
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        # API: Actualizar timeout del watchdog
        if self.path == "/api/settings/timeout":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                mins = int(data.get("timeout", 10))
                set_idle_timeout(mins)
                t_msg = f"{mins} minutos" if mins > 0 else "Desactivado (Nunca)"
                sync_console_status(f"Auto-apagado: {t_msg}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "timeout": mins}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # API: Guardar perfil de modelo modificado
        if self.path == "/api/model/save":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                orig_id = data.get("original_id") or data.get("id")
                ok, msg = save_model_profile(orig_id, data)
                if ok:
                    new_id = data.get("id") or orig_id
                    # Si el modelo renombrado está actualmente activo en GPU, actualizar referencia en caliente
                    if manager.current_model == orig_id and new_id != orig_id:
                        manager.current_model = new_id
                    sync_console_status(f"Perfil '{new_id}' actualizado en config.json")
                    trigger_background_sync()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # API: Crear nuevo perfil de modelo
        if self.path == "/api/model/create":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                profile_id = data.get("id")
                ok, msg = create_model_profile(profile_id, data)
                if ok:
                    sync_console_status(f"Nuevo perfil '{profile_id}' creado en config.json")
                    trigger_background_sync()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # API: Eliminar perfil de modelo existente
        if self.path == "/api/model/delete":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                profile_id = data.get("id")
                # Si el modelo a eliminar está corriendo actualmente en GPU, detenerlo primero
                if manager.current_model == profile_id:
                    print(f"\n[Manager] [-] Deteniendo '{profile_id}' antes de eliminar perfil...")
                    manager.stop_server()

                ok, msg = delete_model_profile(profile_id)
                if ok:
                    sync_console_status(f"Perfil '{profile_id}' eliminado de config.json")
                    trigger_background_sync()
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"success": ok, "message": msg}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_cors()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 3. FILTRO ESTRICTO DE INFERENCIA
        if self.path in INFERENCE_PATHS:
            requested_model = DEFAULT_MODEL
            try:
                data = json.loads(body.decode("utf-8"))
                if "model" in data and data["model"]:
                    requested_model = data["model"]

                # Inyectar System Prompt por defecto y sanitizar mensajes de sistema (protección Jinja)
                cfg = MODELS.get(requested_model, MODELS.get(DEFAULT_MODEL, {}))
                model_sp = cfg.get("system_prompt", None)
                if "messages" in data and isinstance(data["messages"], list):
                    data["messages"] = sanitize_messages(data["messages"], default_system_prompt=model_sp)
                    body = json.dumps(data).encode("utf-8")
                    self.headers["Content-Length"] = str(len(body))
            except Exception:
                pass

            user_agent = self.headers.get("User-Agent", "").lower()
            if "zed" in user_agent:
                client_label = "Zed / Editor"
            elif "chatbox" in user_agent or "electron" in user_agent:
                client_label = "Chatbox"
            else:
                client_label = f"Cliente {self.client_address[0]}"

            print(f"\n[{client_label}] Prompt recibido ({self.path}) -> Modelo: '{requested_model}'")

            ok = manager.ensure_model(requested_model)
            if not ok:
                self.send_response(503)
                self._send_cors()
                self.end_headers()
                self.wfile.write(b'{"error": "No se pudo iniciar el modelo en GPU"}')
                return

            manager.start_inference()
            try:
                self._proxy_request("POST", body, client_label=client_label)
            finally:
                manager.end_inference()
            return

        # 4. Peticiones secundarias
        if manager.current_model is not None:
            self._proxy_request("POST", body)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._send_cors()
            self.end_headers()
            self.wfile.write(b"{}")

    def _proxy_request(self, method, body=None, client_label=None):
        target_url = f"http://{BACKEND_HOST}:{BACKEND_PORT}{self.path}"

        headers = {}
        for k, v in self.headers.items():
            if k.lower() not in ["host", "content-length"]:
                headers[k] = v

        try:
            req = urllib.request.Request(target_url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=300) as backend_res:
                self.send_response(backend_res.status)
                for hk, hv in backend_res.headers.items():
                    if hk.lower() not in ["content-length", "transfer-encoding"]:
                        self.send_header(hk, hv)
                self._send_cors()
                self.end_headers()

                start_t = time.time()
                buffer = b""

                # Streaming chunk por chunk
                while True:
                    chunk = backend_res.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    buffer += chunk
                    manager.touch()

                elapsed = round(time.time() - start_t, 2)
                manager.touch()
                manager.trim_current_model()

                # Mostrar métricas de tokens y velocidad para benchmarks
                if client_label:
                    m_comp = re.search(rb'"completion_tokens"\s*:\s*(\d+)', buffer)
                    if m_comp:
                        total_tokens = int(m_comp.group(1))
                    else:
                        total_tokens = buffer.count(b"data: {")

                    if total_tokens > 0:
                        speed = round(total_tokens / elapsed, 2) if elapsed > 0 else 0
                        print(f"[{client_label}] [OK] Finalizado: {total_tokens} tokens en {elapsed}s ({speed} t/s)")

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self._send_cors()
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.send_response(502)
            self._send_cors()
            self.end_headers()
            self.wfile.write(f'{{"error": "Fallo de conexion con backend: {str(e)}"}}'.encode("utf-8"))


def main():
    print("=" * 64)
    print("                    SIMPLE LLAMA (llama.cpp)")
    print("=" * 64)
    print(f"  • Panel de Control:        http://localhost:{PROXY_PORT}")
    print(f"  • API para Zed / OpenCode:  http://localhost:{PROXY_PORT}/v1")
    print(f"  • Chat Web (al activarse):  http://localhost:{BACKEND_PORT}")
    lan_info = f"ON (http://{get_local_ip()}:{PROXY_PORT})" if is_lan_allowed() else "OFF (Solo Localhost)"
    print(f"  • Red Local (Wi-Fi/LAN):    {lan_info}")
    if MODELS:
        print("  • Modelos registrados:")
        for m_id, cfg in MODELS.items():
            print(f"     - {m_id:<18} ({cfg['ctx']} tokens) [{cfg.get('family', 'General')}]")
    else:
        print("  • Modelos registrados:      Ninguno (No se encontraron modelos)")
    t_mins = get_idle_timeout_minutes()
    timeout_info = f"{t_mins} min de inactividad" if t_mins > 0 else "Desactivado (Nunca)"
    print(f"  • Auto-apagado:             {timeout_info}")
    print("=" * 64)
    print("Presiona Ctrl+C en esta ventana para cerrar el orquestador.\n")
    sync_console_status()

    # Sincronización automática no intrusiva de clientes (OpenCode / Pi) en segundo plano
    trigger_background_sync()

    server = QuietHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)

    # Abrir automáticamente el panel en el navegador al iniciar
    import webbrowser
    import threading
    def _open_dashboard():
        time.sleep(0.5)
        try:
            webbrowser.open(f"http://localhost:{PROXY_PORT}")
        except Exception:
            pass

    threading.Thread(target=_open_dashboard, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando servidor...")
    finally:
        manager.stop_server()
        server.server_close()


if __name__ == "__main__":
    main()
