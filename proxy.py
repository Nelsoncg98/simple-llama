"""
Simple Llama - Orquestador Local de Modelos para llama.cpp
Puerto de escucha: 11435 (Compatible con OpenAI, llama.cpp y Ollama)
"""

import sys
import os
import time
import json
import re
import argparse
import urllib.request
import urllib.error
import urllib.parse
import threading
from html import escape as esc
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
    UI_PORT,
    BACKEND_HOST,
    BACKEND_PORT,
    DEFAULT_CTX,
    MODELS,
    get_default_model,
    reload_models,
    load_settings,
    save_settings,
    get_local_ip,
    get_idle_timeout_minutes,
    set_idle_timeout,
    get_model_details,
    save_model_profile,
    create_model_profile,
    delete_model_profile,
    scan_all_available_gguf,
)
import security
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
    lan_on = security.is_lan_enabled()
    local_ip = get_local_ip()
    mode_tag = " [PÚBLICO]" if security.is_public_mode() else ""
    lan_str = f"LAN: ON ({local_ip}){mode_tag}" if lan_on else "LAN: OFF"
    t_mins = get_idle_timeout_minutes()
    timeout_str = f"Reposo: {t_mins}m" if t_mins > 0 else "Reposo: OFF"

    set_console_title(f"Simple Llama • {model_name} • {lan_str} • {timeout_str}")

    if event_msg:
        print(f"\n[Consola Actualizada] >>> {event_msg}")
        print(f"  • Modelo activo:   {model_name}")
        print(f"  • Red Wi-Fi/LAN:   {'ON (http://' + local_ip + ':' + str(PROXY_PORT) + ')' + mode_tag if lan_on else 'OFF (Solo Localhost)'}")
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


def render_dashboard(role=security.ROLE_LOCAL):
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
            f'<a href="http://localhost:{UI_PORT}" id="chatLink" data-ui-port="{UI_PORT}" target="_blank" class="btn-chat">Abrir Chat Web &#8599;</a> '
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

    # Agrupar modelos por familia dinámicamente (sobre una copia: otros hilos pueden recargar MODELS)
    families = {}
    for m_id, cfg in dict(MODELS).items():
        fam = cfg.get("family", "Otros Modelos")
        if fam not in families:
            families[fam] = []
        families[fam].append((m_id, cfg))

    sections_html = []
    for fam_name, models_in_fam in families.items():
        cards_html = []
        for m_id, cfg in models_in_fam:
            badge_color = esc(str(cfg.get("badge_color", "#38bdf8")), quote=True)
            badge_text = esc(str(cfg.get("badge", f"{cfg['ctx']} tokens")))
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

            m_id_esc = esc(m_id, quote=True)
            cards_html.append(f"""
            <div class="card">
              <div class="card-top">
                <div class="card-header">
                  <h3>{m_id_esc}</h3>
                  {status_warn}
                </div>
                <p>{esc(str(cfg['desc']))}</p>
              </div>
              <div class="card-bottom">
                <form method="POST" action="/load" style="margin:0; flex:1;">
                  <input type="hidden" name="model" value="{m_id_esc}">
                  <button type="submit" class="btn" {btn_attr}>{btn_text_display}</button>
                </form>
                <button type="button" class="btn-config" data-model="{m_id_esc}" onclick="openEditModal(this.dataset.model)" title="Configurar parámetros de {m_id_esc}">
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
            <h2 class="family-title">{esc(str(fam_name))}</h2>
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

    lan_on = security.is_lan_enabled()
    public_mode = security.is_public_mode()
    local_ip = get_local_ip()
    local_ip_esc = esc(local_ip)
    mode_suffix = " · Público" if public_mode else ""
    if lan_on:
        lan_toggle_html = f'''<button type="button" class="btn-toggle btn-toggle-on" onclick="openLanModal()" title="Acceso por red local activo ({local_ip_esc}). Clic para desactivar">
            <span class="dot-ping"></span> Red Local: ON ({local_ip_esc}){mode_suffix}
          </button>'''
    else:
        lan_toggle_html = '''<button type="button" class="btn-toggle btn-toggle-off" onclick="openLanModal()" title="Clic para permitir acceso desde celular/tablet en tu red">
            <span class="dot-ping"></span> Red Local: OFF
          </button>'''

    if role == security.ROLE_ADMIN:
        lan_toggle_html += (
            '<form method="POST" action="/logout" style="display:inline; margin-left:6px;">'
            '<button type="submit" class="btn-cancel" title="Cerrar la sesión de administrador en este dispositivo">Cerrar sesión</button>'
            '</form>'
        )

    lan_state = json.dumps({
        "lan": lan_on,
        "public": public_mode,
        "ip": local_ip,
        "port": PROXY_PORT,
        "uiPort": UI_PORT,
        "role": role,
        "lastNotice": security.last_notice_id(),
    }).replace("</", "<\\/")

    html = html.replace("{{STATUS_LABEL}}", esc(status_label))
    html = html.replace("{{LAN_TOGGLE}}", lan_toggle_html)
    html = html.replace("{{LAN_STATE_JSON}}", lan_state)
    html = html.replace("{{TIMEOUT_SELECTOR}}", timeout_selector_html)
    html = html.replace("{{ACTION_BUTTONS}}", action_buttons)
    html = html.replace("{{MODEL_SECTIONS}}", "\n".join(sections_html))
    html = html.replace("{{PROXY_PORT}}", str(PROXY_PORT))
    return html


# Rutas de la API "pública" (las usan Zed, OpenCode, Chatbox...): permiten CORS abierto y,
# en modo público, son las únicas accesibles para clientes sin PIN (además del login).
PUBLIC_API_EXACT = {"/v1/models", "/models", "/api/tags", "/health"}


def _is_public_api_path(path):
    path = path.split("?", 1)[0]
    return (
        path in PUBLIC_API_EXACT
        or path in INFERENCE_PATHS
        or path.startswith("/props")
        or path.startswith("/v1/")
    )


LOGIN_MESSAGES = {
    "fail": "PIN incorrecto.",
    "locked": "Demasiados intentos fallidos. Espera unos segundos e inténtalo de nuevo.",
    "disabled": "La administración remota está deshabilitada en este servidor.",
}


def render_public_page(message=None):
    """Página para clientes en modo público: datos de conexión y login con PIN."""
    template_path = os.path.join(BASE_DIR, "templates", "public.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:
        return "<h1>Error: No se encontró templates/public.html</h1>"

    ip = get_local_ip()
    active_name = manager.current_model or "En reposo (se carga al recibir el primer prompt)"
    is_active = manager.current_model is not None
    dot_class = "dot-active" if is_active else "dot-idle"

    badges = []
    for m in dict(MODELS).keys():
        badges.append(f'<span class="model-badge">{esc(m)}</span>')
    badges_html = " ".join(badges) if badges else '<span class="muted-text">Ninguno</span>'

    if security.has_admin_pin():
        msg_html = f'<p class="err-msg">{esc(message)}</p>' if message else ""
        admin_html = (
            '<p class="muted-text">Ingresa tu PIN de administrador para desbloquear el panel completo:</p>'
            '<form method="POST" action="/login" class="form-group">'
            '<input type="password" name="pin" class="input-pin" placeholder="PIN de administrador" autocomplete="current-password" required>'
            '<button type="submit" class="btn-submit">Desbloquear</button>'
            '</form>' + msg_html
        )
    else:
        admin_html = '<p class="muted-text">La administración remota está deshabilitada en este servidor. Administra desde la PC local.</p>'

    if is_active:
        chat_button = f'<div style="margin-top:14px;"><a href="http://{esc(ip)}:{UI_PORT}" target="_blank" class="btn-chat">Abrir Chat Web &#8599;</a></div>'
    else:
        chat_button = '<p class="muted-text" style="margin-top:10px; font-size:12px;">Chat web disponible cuando el administrador active un modelo.</p>'

    html = html.replace("{{IP}}", esc(ip))
    html = html.replace("{{PORT}}", str(PROXY_PORT))
    html = html.replace("{{ACTIVE_MODEL}}", esc(active_name))
    html = html.replace("{{DOT_CLASS}}", dot_class)
    html = html.replace("{{CHAT_BUTTON}}", chat_button)
    html = html.replace("{{MODELS_BADGES}}", badges_html)
    html = html.replace("{{ADMIN_SECTION}}", admin_html)
    return html


def render_lan_disabled_page():
    """Página mostrada cuando un cliente remoto intenta acceder con la red local desactivada."""
    template_path = os.path.join(BASE_DIR, "templates", "lan_disabled.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<h1 style='font-family:sans-serif'>RED LOCAL DESACTIVADA</h1>"


# --- SERVIDOR HTTP MULTIHILO Y SILENCIOSO (EVITA BLOQUEOS EN STREAMING) ---
class QuietHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 64
    # En Windows SO_REUSEADDR permite que OTRO proceso se enganche al mismo puerto: se desactiva
    allow_reuse_address = (os.name != "nt")

    def handle_error(self, request, client_address):
        exc_type, _, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            return
        super().handle_error(request, client_address)


class GuardedHandler(BaseHTTPRequestHandler):
    """Base común: puerta de acceso (IP privada, Host, rol, Origin), helpers de respuesta y reenvío al backend."""

    role = None

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass

    def log_message(self, format, *args):
        # Silenciar logs verbosos internos de HTTP
        pass

    # ------------------------- respuestas -------------------------
    def _send_cors(self, public_api=False):
        """Cabeceras comunes. CORS abierto SOLO en la API pública; panel y administración son same-origin."""
        if public_api:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send_bytes(self, code, payload, content_type, public_api=False, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for hk, hv in (extra_headers or []):
            self.send_header(hk, hv)
        self._send_cors(public_api)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code, obj, public_api=False):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send_bytes(code, payload, "application/json; charset=utf-8", public_api)

    def _send_html(self, code, html_text):
        self._send_bytes(code, html_text.encode("utf-8"), "text/html; charset=utf-8")

    def _redirect(self, location, code=303, extra_headers=None):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for hk, hv in (extra_headers or []):
            self.send_header(hk, hv)
        self.end_headers()

    # ------------------------- puerta de acceso -------------------------
    def _gate(self, enforce_origin=False):
        """
        Valida: Host permitido (anti DNS-rebinding), IP privada, LAN activa y rol.
        Con enforce_origin valida además Origin/Sec-Fetch-Site (anti CSRF).
        Retorna True si la petición puede continuar; si no, ya respondió el error.
        """
        ip = self.client_address[0]
        host = self.headers.get("Host")
        if not security.host_allowed(host):
            self._send_json(403, {"error": "Host no permitido."})
            return False

        role = security.classify(ip, self.headers.get("Cookie"))
        if role is None:
            if security.is_private_ip(ip) and not security.is_lan_enabled():
                if "text/html" in (self.headers.get("Accept") or ""):
                    self._send_html(403, render_lan_disabled_page())
                    return False
                msg = "Acceso por red local desactivado (OFF). Actívalo en el panel web desde la PC."
            else:
                msg = "Solo se permiten conexiones desde la red local."
            self._send_json(403, {"error": msg})
            return False
        self.role = role

        if enforce_origin and not security.origin_allowed(
            self.headers.get("Origin"), host, self.headers.get("Sec-Fetch-Site")
        ):
            self._send_json(403, {"error": "Origen de la petición no permitido."})
            return False
        return True

    def _require_admin(self):
        """Exige rol con permisos de administración (PC local, sesión con PIN o modo Hogar)."""
        if security.can_admin(self.role):
            return True
        if "text/html" in (self.headers.get("Accept") or ""):
            self._send_html(403, "<h3 style='font-family:sans-serif'>Requiere permisos de administrador (PC del servidor o PIN).</h3>")
        else:
            self._send_json(403, {"error": "Requiere permisos de administrador (PC del servidor o PIN)."})
        return False

    def _read_body(self):
        """Lee el cuerpo con límite de tamaño. Retorna bytes, o None si ya respondió un error."""
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw) if raw else 0
        except ValueError:
            self._send_json(400, {"error": "Content-Length inválido."})
            return None
        if length < 0:
            self._send_json(400, {"error": "Content-Length inválido."})
            return None
        if length > security.MAX_BODY_BYTES:
            self._send_json(413, {"error": "Cuerpo de la petición demasiado grande."})
            return None
        return self.rfile.read(length) if length else b""

    # ------------------------- reenvío al backend -------------------------
    def _proxy_request(self, method, body=None, client_label=None, public_api=False):
        target_url = f"http://{BACKEND_HOST}:{BACKEND_PORT}{self.path}"

        headers = {}
        for k, v in self.headers.items():
            # No se reenvía la cookie de sesión del orquestador al backend
            if k.lower() not in ("host", "content-length", "cookie", "connection"):
                headers[k] = v

        try:
            req = urllib.request.Request(target_url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=300) as backend_res:
                self.send_response(backend_res.status)
                for hk, hv in backend_res.headers.items():
                    lk = hk.lower()
                    if lk in ("content-length", "transfer-encoding", "connection") or lk.startswith("access-control-"):
                        continue
                    self.send_header(hk, hv)
                self._send_cors(public_api)
                self.end_headers()

                start_t = time.time()
                tail = b""          # Solo la cola de la respuesta (el uso de tokens llega al final)
                stream_events = 0

                # Streaming chunk por chunk
                while True:
                    chunk = backend_res.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    tail = (tail + chunk)[-8192:]
                    stream_events += chunk.count(b"data: {")
                    manager.touch()

                elapsed = round(time.time() - start_t, 2)
                manager.touch()
                manager.trim_current_model()

                # Mostrar métricas de tokens y velocidad para benchmarks
                if client_label:
                    m_comp = re.search(rb'"completion_tokens"\s*:\s*(\d+)', tail)
                    if m_comp:
                        total_tokens = int(m_comp.group(1))
                    else:
                        total_tokens = stream_events

                    if total_tokens > 0:
                        speed = round(total_tokens / elapsed, 2) if elapsed > 0 else 0
                        print(f"[{client_label}] [OK] Finalizado: {total_tokens} tokens en {elapsed}s ({speed} t/s)")

        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            ctype = e.headers.get("Content-Type") if e.headers else None
            if ctype:
                self.send_header("Content-Type", ctype)
            self._send_cors(public_api)
            self.end_headers()
            self.wfile.write(e.read())
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            raise
        except Exception as e:
            try:
                self._send_json(502, {"error": f"Fallo de conexion con backend: {e}"}, public_api)
            except Exception:
                pass


class ProxyHandler(GuardedHandler):
    """Puerto 11435: API compatible con OpenAI/Ollama + panel de control."""

    def do_OPTIONS(self):
        if not self._gate():
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self._send_cors(public_api=_is_public_api_path(self.path))
        self.end_headers()

    def do_GET(self):
        if not self._gate():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        is_admin = security.can_admin(self.role)

        # Ignorar peticiones de precarga especulativa de navegadores (Prefetch / Prerender)
        purpose = self.headers.get("Purpose", "") or self.headers.get("Sec-Purpose", "")
        if "prefetch" in purpose.lower() or "prerender" in purpose.lower():
            self.send_response(204)
            self.end_headers()
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # 1. Lista de modelos (Estándar OpenAI y llama.cpp para Zed / OpenCode)
        if path in ("/v1/models", "/models"):
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
                for m_id, cfg in dict(MODELS).items()
            ]
            self._send_json(200, {"object": "list", "data": model_list}, public_api=True)
            return

        # 2. Lista de modelos para clientes formato Ollama (/api/tags)
        if path == "/api/tags":
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
                for m_id, cfg in dict(MODELS).items()
            ]
            self._send_json(200, {"models": models_data}, public_api=True)
            return

        # 3. Health check
        if path == "/health":
            self._send_bytes(200, b"OK - Mini-Orquestador Activo", "text/plain", public_api=True)
            return

        # 4. El puerto 11435 es SIEMPRE el Dashboard (clientes sin permisos ven solo la página pública)
        if path in ("/", "/dashboard"):
            if is_admin:
                self._send_html(200, render_dashboard(self.role))
            else:
                q = urllib.parse.parse_qs(parsed.query)
                msg = LOGIN_MESSAGES.get(q.get("login", [""])[0])
                self._send_html(200, render_public_page(msg))
            return

        # 5. Endpoint /props (Auto-descubrimiento exacto de contexto para Zed)
        if path.startswith("/props"):
            active_name = manager.current_model or get_default_model()
            active_cfg = MODELS.get(active_name) or {}
            active_ctx = active_cfg.get("ctx", DEFAULT_CTX)
            props_data = {
                "default_generation_settings": {
                    "n_ctx": active_ctx,
                    "params": {"n_predict": -1, "max_tokens": active_ctx}
                },
                "total_slots": 1,
                "model_alias": active_name or "",
                "chat_template_caps": {
                    "supports_tools": True,
                    "supports_images": active_cfg.get("vision", False),
                }
            }
            self._send_json(200, props_data, public_api=True)
            return

        # --- Desde aquí todo requiere permisos de administración ---
        if not self._require_admin():
            return

        # 6. API: Estado del interruptor LAN + notificaciones pendientes
        if path == "/api/lan/status":
            q = urllib.parse.parse_qs(parsed.query)
            try:
                since = int(q.get("since", ["0"])[0])
            except ValueError:
                since = 0
            self._send_json(200, {
                "lan": security.is_lan_enabled(),
                "public": security.is_public_mode(),
                "ip": get_local_ip(),
                "role": self.role,
                "notices": security.get_notices(since),
                "lastNotice": security.last_notice_id(),
            })
            return

        # 7. API: Detalles de un modelo para el editor web
        if path == "/api/model/details":
            q = urllib.parse.parse_qs(parsed.query)
            m_id = q.get("model", [None])[0]
            details = get_model_details(m_id) if m_id else None
            if details:
                self._send_json(200, details)
            else:
                self._send_json(404, {"error": "Modelo no encontrado"})
            return

        # 8. API: Catálogo completo de archivos .gguf para los modales
        if path == "/api/catalog":
            self._send_json(200, scan_all_available_gguf())
            return

        # 9. API: Consultar si hubo un error al arrancar el último modelo
        if path == "/api/last-error":
            self._send_json(200, {"error": manager.get_last_error()})
            return

        # Si el modelo está corriendo y piden un recurso estático (slots, metrics, etc.), reenviar al backend
        if manager.current_model is not None:
            self._proxy_request("GET")
        else:
            # Si piden APIs en reposo no contempladas arriba, responder JSON neutro
            if path.startswith("/slots") or path.startswith("/api"):
                self._send_json(503, {"error": "Modelo en reposo (GPU libre)"})
                return
            # Si no hay modelo y piden otra página, devolver al Dashboard
            self._redirect("/", code=302)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        is_inference = path in INFERENCE_PATHS

        # El Origin se exige en todo POST salvo la API de inferencia (que usan clientes de otros orígenes)
        if not self._gate(enforce_origin=not is_inference):
            return

        # 0. Login / logout de administrador remoto (PIN)
        if path == "/login":
            body = self._read_body()
            if body is None:
                return
            pin = urllib.parse.parse_qs(body.decode("utf-8", errors="ignore")).get("pin", [""])[0]
            ip = self.client_address[0]
            if not security.has_admin_pin():
                self._redirect("/?login=disabled")
                return
            if security.login_blocked_seconds(ip) > 0:
                self._redirect("/?login=locked")
                return
            token = security.try_login(ip, pin)
            if token:
                cookie = f"{security.SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={security.SESSION_TTL_SECONDS}"
                print(f"\n[Seguridad] Sesión de administrador iniciada desde {ip}")
                self._redirect("/", extra_headers=[("Set-Cookie", cookie)])
            else:
                print(f"\n[Seguridad] [!] PIN incorrecto desde {ip}")
                self._redirect("/?login=locked" if security.login_blocked_seconds(ip) > 0 else "/?login=fail")
            return

        if path == "/logout":
            security.end_session(security.get_cookie_token(self.headers.get("Cookie")))
            expired = f"{security.SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            self._redirect("/", extra_headers=[("Set-Cookie", expired)])
            return

        # 3. FILTRO ESTRICTO DE INFERENCIA (único POST permitido a clientes sin permisos de administración)
        if is_inference:
            self._handle_inference()
            return

        # --- Desde aquí todo requiere permisos de administración ---
        if not self._require_admin():
            return

        # 1. Cargar modelo explícitamente desde el panel web
        if path == "/load":
            body = self._read_body()
            if body is None:
                return
            params = urllib.parse.parse_qs(body.decode("utf-8", errors="ignore"))
            target = params.get("model", [get_default_model()])[0]
            if target:
                print(f"\n[Panel Web] Clic manual del usuario -> Cargando '{target}' en GPU...")
                ok = manager.ensure_model(target, force_reload=True)
                if ok:
                    sync_console_status(f"Modelo cargado: '{target}'")
            self._redirect("/")
            return

        # 2. Descargar modelo / Liberar GPU desde el panel web
        if path == "/unload":
            print("\n[Panel Web] Clic manual del usuario -> Liberando GPU y RAM...")
            manager.stop_server()
            sync_console_status("GPU y memoria liberadas (0 MB VRAM)")
            self._redirect("/")
            return

        body = self._read_body()
        if body is None:
            return
        body = body or b"{}"

        # API: Encender / apagar la Red Local (modal de confirmación en el dashboard)
        if path == "/api/lan/toggle":
            try:
                data = json.loads(body.decode("utf-8"))
                want = bool(data.get("enable"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                self._send_json(400, {"success": False, "message": "Petición inválida"})
                return

            if want:
                if security.is_lan_enabled():
                    self._send_json(200, {"success": True, "lan": True, "message": "La red local ya estaba activa"})
                    return
                if self.role != security.ROLE_LOCAL:
                    self._send_json(403, {"success": False, "message": "Solo se puede activar desde la PC"})
                    return
                ok, msg, need_force = security.enable_lan(force=bool(data.get("force")))
                if not ok:
                    self._send_json(409, {"success": False, "requiresForce": need_force, "message": msg})
                    return
                sync_console_status("Red Local ACTIVADA (ON): cualquier dispositivo de la red puede conectarse")
            else:
                security.disable_lan()
                sync_console_status("Red Local DESACTIVADA (OFF): solo localhost")
            self._send_json(200, {"success": True, "lan": security.is_lan_enabled(),
                                  "message": "Red local activada" if security.is_lan_enabled() else "Red local desactivada"})
            return

        # API: Actualizar timeout del watchdog
        if path == "/api/settings/timeout":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                mins = int(data.get("timeout", 10))
                set_idle_timeout(mins)
                t_msg = f"{mins} minutos" if mins > 0 else "Desactivado (Nunca)"
                sync_console_status(f"Auto-apagado: {t_msg}")
                self._send_json(200, {"success": True, "timeout": mins})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        # API: Guardar perfil de modelo modificado
        if path == "/api/model/save":
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
                self._send_json(200 if ok else 400, {"success": ok, "message": msg})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        # API: Crear nuevo perfil de modelo
        if path == "/api/model/create":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
                profile_id = data.get("id")
                ok, msg = create_model_profile(profile_id, data)
                if ok:
                    sync_console_status(f"Nuevo perfil '{profile_id}' creado en config.json")
                    trigger_background_sync()
                self._send_json(200 if ok else 400, {"success": ok, "message": msg})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        # API: Eliminar perfil de modelo existente
        if path == "/api/model/delete":
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
                self._send_json(200 if ok else 400, {"success": ok, "message": msg})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        # 4. Peticiones secundarias
        if manager.current_model is not None:
            self._proxy_request("POST", body)
        else:
            self._send_json(200, {})

    def _handle_inference(self):
        """Prompt real: aplica los límites del modo público a invitados, asegura el modelo y reenvía al backend."""
        body = self._read_body()
        if body is None:
            return

        ip = self.client_address[0]
        is_guest = self.role == security.ROLE_GUEST
        requested_model = get_default_model()

        data = None
        try:
            parsed = json.loads(body.decode("utf-8")) if body else None
            if isinstance(parsed, dict):
                data = parsed
        except (ValueError, UnicodeDecodeError):
            data = None

        if is_guest and data is None:
            self._send_json(400, {"error": "Cuerpo JSON inválido."}, public_api=True)
            return

        if data is not None:
            if data.get("model"):
                requested_model = data["model"]

            if is_guest:
                # Modo público: nadie puede forzar el cambio de modelo (recargas de GPU) ni acaparar la generación
                locked = manager.current_model or get_default_model()
                requested_model = locked
                if locked:
                    data["model"] = locked
                cap = security.state.max_tokens
                limit_keys = ("max_tokens", "max_completion_tokens", "n_predict")
                for key in limit_keys:
                    val = data.get(key)
                    if isinstance(val, (int, float)) and (val > cap or val < 0):
                        data[key] = cap
                if not any(isinstance(data.get(k), (int, float)) for k in limit_keys):
                    data["max_tokens"] = cap

            # Inyectar System Prompt por defecto y sanitizar mensajes de sistema (protección Jinja)
            cfg = MODELS.get(requested_model) or MODELS.get(get_default_model()) or {}
            model_sp = cfg.get("system_prompt", None)
            if "messages" in data and isinstance(data["messages"], list):
                data["messages"] = sanitize_messages(data["messages"], default_system_prompt=model_sp)
            body = json.dumps(data).encode("utf-8")

        user_agent = self.headers.get("User-Agent", "").lower()
        if "zed" in user_agent:
            client_label = "Zed / Editor"
        elif "chatbox" in user_agent or "electron" in user_agent:
            client_label = "Chatbox"
        else:
            client_label = f"Cliente {ip}"

        print(f"\n[{client_label}] Prompt recibido ({self.path}) -> Modelo: '{requested_model}'")

        acquired = False
        if is_guest:
            if not security.acquire_slot(ip):
                self._send_json(429, {"error": "Demasiadas peticiones simultáneas desde tu dispositivo. Espera a que termine la actual."}, public_api=True)
                return
            acquired = True

        try:
            ok = manager.ensure_model(requested_model)
            if not ok:
                self._send_json(503, {"error": manager.peek_last_error() or "No se pudo iniciar el modelo en GPU"}, public_api=True)
                return

            manager.start_inference()
            try:
                self._proxy_request("POST", body, client_label=client_label, public_api=True)
            finally:
                manager.end_inference()
        finally:
            if acquired:
                security.release_slot(ip)


class UIGatewayHandler(GuardedHandler):
    """
    Puerto 11436: UI nativa de llama.cpp (llama-ui). Reenvía a llama-server (127.0.0.1:11437) aplicando
    las mismas reglas de acceso: respeta el interruptor LAN y, en modo público, exige PC local o PIN.
    """

    def _forward(self, method):
        has_body = method in ("POST", "PUT", "PATCH", "DELETE")
        if not self._gate(enforce_origin=has_body):
            return
        if manager.current_model is None:
            self._send_html(503, (
                "<div style='font-family:system-ui,sans-serif; background:#050507; color:#f4f4f5; min-height:100vh; display:flex; align-items:center; justify-content:center; text-align:center; padding:24px;'>"
                "<div style='background:#0d0d10; border:1px solid rgba(255,255,255,0.07); padding:32px; border-radius:12px; max-width:440px;'>"
                "<h3 style='margin-bottom:8px;'>Modelo en Reposo</h3>"
                "<p style='color:#a1a1aa; font-size:14px; line-height:1.5;'>El chat estará disponible cuando el administrador cargue un modelo en la GPU.</p>"
                "</div></div>"
            ))
            return
        body = None
        if has_body:
            body = self._read_body()
            if body is None:
                return
        manager.touch()
        self._proxy_request(method, body)

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PUT(self):
        self._forward("PUT")

    def do_DELETE(self):
        self._forward("DELETE")

    def do_OPTIONS(self):
        if not self._gate():
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self._send_cors(public_api=False)
        self.end_headers()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Simple Llama - Orquestador local para llama.cpp")
    parser.add_argument(
        "--lan", action="store_true",
        help="Modo PÚBLICO/servidor: la red local inicia ACTIVA. Los clientes sin PIN solo pueden generar (API /v1); "
             "el panel exige la PC del servidor o el PIN de administrador.",
    )
    parser.add_argument(
        "--admin-pin", default=os.environ.get("SIMPLE_LLAMA_PIN"),
        help="PIN para administrar remotamente en modo público (o variable de entorno SIMPLE_LLAMA_PIN).",
    )
    parser.add_argument("--max-tokens", type=int, default=2048, help="Tope de tokens por respuesta para invitados (modo público).")
    parser.add_argument("--max-concurrent", type=int, default=2, help="Peticiones simultáneas por invitado (modo público).")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador al iniciar.")
    return parser.parse_args(argv)


def start_ui_gateway():
    """Levanta el puerto de la UI de llama.cpp (11436) como pasarela con control de acceso."""
    try:
        gateway = QuietHTTPServer((PROXY_HOST, UI_PORT), UIGatewayHandler)
    except OSError as e:
        print(f"[!] No se pudo abrir el puerto {UI_PORT} para la UI de llama.cpp ({e}). El resto sigue funcionando.")
        return None
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    return gateway


def main():
    args = parse_args()
    if args.admin_pin and len(str(args.admin_pin)) < 4:
        print("[!] El PIN de administrador debe tener al menos 4 caracteres.")
        sys.exit(1)
    security.configure(
        public_mode=args.lan,
        admin_pin=args.admin_pin,
        max_tokens=args.max_tokens,
        max_concurrent=args.max_concurrent,
    )

    print("=" * 64)
    print("                    SIMPLE LLAMA (llama.cpp)")
    print("=" * 64)
    print(f"  • Panel de Control:        http://localhost:{PROXY_PORT}")
    print(f"  • API para Zed / OpenCode:  http://localhost:{PROXY_PORT}/v1")
    print(f"  • Chat Web (al activarse):  http://localhost:{UI_PORT}")
    if args.lan:
        pin_info = "habilitada con PIN" if security.has_admin_pin() else "DESHABILITADA (sin PIN: solo desde esta PC)"
        print(f"  • MODO PÚBLICO:            ON (http://{get_local_ip()}:{PROXY_PORT})")
        print(f"      - Invitados: solo generar (máx. {args.max_tokens} tokens, {args.max_concurrent} simultáneas)")
        print(f"      - Administración remota: {pin_info}")
    else:
        print("  • Red Local (Wi-Fi/LAN):    OFF (Solo Localhost) — actívala desde el panel")
    reload_models()
    if MODELS:
        print("  • Modelos registrados:")
        for m_id, cfg in dict(MODELS).items():
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

    # En modo Hogar, apagar la LAN automáticamente si cambia la red (cafetería, otro Wi-Fi, VPN...)
    if not args.lan:
        security.start_network_watcher(on_change=sync_console_status)

    server = QuietHTTPServer((PROXY_HOST, PROXY_PORT), ProxyHandler)
    gateway = start_ui_gateway()

    # Abrir automáticamente el panel en el navegador al iniciar (no en servidores públicos)
    if not args.lan and not args.no_browser:
        import webbrowser

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
        if gateway:
            gateway.shutdown()
            gateway.server_close()


if __name__ == "__main__":
    main()
