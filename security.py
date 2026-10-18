"""
Control de acceso de Simple Llama (security.py)

Modelo de seguridad:
  * Modo HOGAR (por defecto): arranca con LAN en OFF (solo la PC). Al activar LAN, cualquier
    dispositivo de la red local puede usar y administrar todo (celular, tablet, etc.).
  * Modo PÚBLICO (--lan): arranca con LAN en ON. Cualquiera en la red local puede GENERAR
    (API /v1 para Zed, OpenCode, etc.), pero ver/administrar el panel exige estar en la PC del
    servidor o iniciar sesión con el PIN de administrador.
  * En ambos modos solo se aceptan clientes de IP privada/loopback (nunca IPs públicas).
  * El estado LAN NUNCA se guarda en disco: reiniciar siempre vuelve a OFF (o a --lan).
"""

import hmac
import http.cookies
import ipaddress
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

from config import get_local_ip

SESSION_COOKIE = "sl_session"
SESSION_TTL_SECONDS = 12 * 3600
MAX_BODY_BYTES = 32 * 1024 * 1024  # Tope de cuerpo de petición (evita agotar la RAM)

# Roles de cliente
ROLE_LOCAL = "local"    # Se conecta desde la propia PC (loopback)
ROLE_ADMIN = "admin"    # Remoto con sesión de PIN válida
ROLE_HOME = "home"      # Remoto en modo Hogar (acceso total por diseño)
ROLE_GUEST = "guest"    # Remoto en modo Público sin PIN: solo puede generar

_lock = threading.RLock()


class _State:
    lan_enabled = False
    public_mode = False
    admin_pin = None
    max_tokens = 2048
    max_concurrent = 2


state = _State()


# --------------------------------------------------------------------------- #
# Configuración de modo
# --------------------------------------------------------------------------- #
def configure(public_mode=False, admin_pin=None, max_tokens=2048, max_concurrent=2):
    """Configura el modo de ejecución al arrancar. En modo público la LAN inicia en ON."""
    with _lock:
        state.public_mode = bool(public_mode)
        state.admin_pin = str(admin_pin) if admin_pin else None
        state.max_tokens = max(16, int(max_tokens))
        state.max_concurrent = max(1, int(max_concurrent))
        state.lan_enabled = bool(public_mode)


def is_public_mode():
    return state.public_mode


def is_lan_enabled():
    return state.lan_enabled


def has_admin_pin():
    return bool(state.admin_pin)


# --------------------------------------------------------------------------- #
# Notificaciones para el dashboard
# --------------------------------------------------------------------------- #
_notices = []
_notice_seq = 0


def push_notice(level, message):
    """Guarda una notificación (info/warn/error) que el dashboard mostrará en un aviso."""
    global _notice_seq
    with _lock:
        _notice_seq += 1
        _notices.append({"id": _notice_seq, "level": level, "message": message, "ts": time.time()})
        del _notices[:-20]
        return _notice_seq


def get_notices(since_id=0):
    with _lock:
        return [n for n in _notices if n["id"] > since_id]


def last_notice_id():
    with _lock:
        return _notice_seq


# --------------------------------------------------------------------------- #
# Validación de IP / Host / Origin
# --------------------------------------------------------------------------- #
def _parse_ip(value):
    try:
        ip = ipaddress.ip_address(value.split("%")[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def is_loopback_ip(value):
    ip = _parse_ip(value)
    return bool(ip and ip.is_loopback)


def is_private_ip(value):
    """True para loopback y rangos privados/link-local (nunca IPs públicas de internet)."""
    ip = _parse_ip(value)
    return bool(ip and (ip.is_loopback or ip.is_private or ip.is_link_local))


def host_allowed(host_header):
    """
    Protección contra DNS-rebinding: el encabezado Host debe ser localhost, una IP privada
    o el nombre de esta máquina. Un dominio externo apuntando a 127.0.0.1 queda rechazado.
    """
    if not host_header:
        return True
    host = host_header.strip()
    if host.startswith("["):  # IPv6 literal [::1]:puerto
        host = host[1:].split("]")[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    host = host.lower().rstrip(".")
    if host == "localhost":
        return True
    if _parse_ip(host) is not None:
        return is_private_ip(host)
    try:
        if host == socket.gethostname().lower():
            return True
    except Exception:
        pass
    return host.endswith(".local")


def origin_allowed(origin_header, host_header, sec_fetch_site=None):
    """
    Anti-CSRF para acciones que cambian estado: si el navegador envía Origin, debe coincidir
    con el Host al que se conectó. Clientes sin Origin (curl, Zed, scripts) pasan.
    """
    if sec_fetch_site and sec_fetch_site.lower() == "cross-site":
        return False
    if not origin_header:
        return True
    if origin_header.strip().lower() == "null":
        return False
    try:
        origin_netloc = urlparse(origin_header).netloc.lower()
    except Exception:
        return False
    return bool(origin_netloc) and origin_netloc == (host_header or "").strip().lower()


# --------------------------------------------------------------------------- #
# Sesiones de administrador (PIN)
# --------------------------------------------------------------------------- #
_sessions = {}          # token -> expira (epoch)
_login_failures = {}    # ip -> (fallos, bloqueado_hasta)


def _purge_sessions():
    now = time.time()
    for tok in [t for t, exp in _sessions.items() if exp < now]:
        _sessions.pop(tok, None)


def login_blocked_seconds(ip):
    with _lock:
        _, until = _login_failures.get(ip, (0, 0))
        return max(0, int(until - time.time()))


def try_login(ip, pin):
    """Verifica el PIN con comparación de tiempo constante y bloqueo progresivo por IP. Retorna token o None."""
    with _lock:
        if not state.admin_pin:
            return None
        if login_blocked_seconds(ip) > 0:
            return None
        if hmac.compare_digest(str(pin or "").encode("utf-8"), state.admin_pin.encode("utf-8")):
            _login_failures.pop(ip, None)
            _purge_sessions()
            token = secrets.token_urlsafe(32)
            _sessions[token] = time.time() + SESSION_TTL_SECONDS
            return token
        fails, _ = _login_failures.get(ip, (0, 0))
        fails += 1
        lock_for = 0 if fails < 5 else min(300, 30 * (fails - 4))
        _login_failures[ip] = (fails, time.time() + lock_for)
        return None


def valid_session(token):
    if not token:
        return False
    with _lock:
        exp = _sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            _sessions.pop(token, None)
            return False
        return True


def end_session(token):
    with _lock:
        _sessions.pop(token, None)


def get_cookie_token(cookie_header):
    if not cookie_header:
        return None
    try:
        jar = http.cookies.SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else None
    except http.cookies.CookieError:
        return None


# --------------------------------------------------------------------------- #
# Roles y permisos
# --------------------------------------------------------------------------- #
def classify(client_ip, cookie_header):
    """Devuelve el rol del cliente, o None si no tiene permiso de conexión alguna."""
    if is_loopback_ip(client_ip):
        return ROLE_LOCAL
    if not is_private_ip(client_ip):
        return None  # IPs públicas: jamás
    if not state.lan_enabled:
        return None
    if valid_session(get_cookie_token(cookie_header)):
        return ROLE_ADMIN
    return ROLE_GUEST if state.public_mode else ROLE_HOME


def can_admin(role):
    return role in (ROLE_LOCAL, ROLE_ADMIN, ROLE_HOME)


# --------------------------------------------------------------------------- #
# Límite de concurrencia por cliente (modo público)
# --------------------------------------------------------------------------- #
_active = {}


def acquire_slot(ip):
    with _lock:
        if _active.get(ip, 0) >= state.max_concurrent:
            return False
        _active[ip] = _active.get(ip, 0) + 1
        return True


def release_slot(ip):
    with _lock:
        n = _active.get(ip, 0) - 1
        if n > 0:
            _active[ip] = n
        else:
            _active.pop(ip, None)


# --------------------------------------------------------------------------- #
# Red: firma de red actual y categoría (Windows)
# --------------------------------------------------------------------------- #
def _run_quiet(cmd, timeout=5):
    try:
        res = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return res.stdout or ""
    except Exception:
        return ""


def get_network_category():
    """Retorna 'Public' si algún perfil de red activo de Windows es Público; 'Private' si no; None si no se sabe."""
    if sys.platform != "win32":
        return None
    out = _run_quiet([
        "powershell", "-NoProfile", "-Command",
        "(Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory.ToString() }) -join ','",
    ], timeout=8)
    cats = [c.strip() for c in out.strip().split(",") if c.strip()]
    if not cats:
        return None
    return "Public" if "Public" in cats else "Private"


def _current_ssid():
    if sys.platform != "win32":
        return None
    out = _run_quiet(["netsh", "wlan", "show", "interfaces"], timeout=4)
    m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", out, re.MULTILINE)
    return m.group(1) if m else None


def network_signature():
    """Identifica la red actual: subred /24 + SSID Wi-Fi (si existe). Un cambio implica otra red."""
    ip = get_local_ip()
    try:
        subnet = str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        subnet = ip
    return (subnet, _current_ssid())


def is_firewall_allowed(port=11435):
    """Verifica si en Windows python.exe o el puerto están autorizados en el Firewall para redes privadas."""
    if sys.platform != "win32":
        return True
    try:
        ps_cmd = (
            "(Get-NetFirewallRule -Enabled True -Direction Inbound | "
            "Where-Object { ($_.DisplayName -like '*python*' -or ($_.LocalPort -match '11435')) -and "
            "($_.Profile -match 'Private' -or $_.Profile -eq 'Any') }) -ne $null"
        )
        out = _run_quiet(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=4)
        return "True" in out
    except Exception:
        return True


def ensure_firewall_rule():
    """Si en Windows python no tiene permiso en redes privadas, pide permiso UAC nativo de administrador."""
    if sys.platform != "win32":
        return True
    if is_firewall_allowed(11435):
        return True

    try:
        import ctypes
        args = 'advfirewall firewall add rule name="Python-Llama" dir=in action=allow protocol=TCP localport=11435,11436 profile=any'
        res = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "netsh.exe",
            args,
            None,
            0  # SW_HIDE
        )
        time.sleep(1)
        return is_firewall_allowed(11435)
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Interruptor LAN
# --------------------------------------------------------------------------- #
_baseline = {"signature": None, "category": None}


def enable_lan(force=False):
    """
    Activa la LAN (modo Hogar). Si Windows clasifica la red como Pública se exige force=True
    (confirmación explícita). Asegura automáticamente la regla de firewall en Windows.
    """
    category = get_network_category()
    if category == "Public" and not force:
        return False, "Windows marca esta red como PÚBLICA. Confirma de nuevo si realmente quieres abrirla.", True

    if sys.platform == "win32":
        ensure_firewall_rule()

    with _lock:
        state.lan_enabled = True
        _baseline["signature"] = network_signature()
        _baseline["category"] = category
    return True, "Red local ACTIVADA", False


def disable_lan(reason=None):
    with _lock:
        was_on = state.lan_enabled
        state.lan_enabled = False
        _sessions.clear()
    if was_on and reason:
        push_notice("warn", reason)
    return was_on


def start_network_watcher(on_change=None, interval=5):
    """
    Hilo de vigilancia (solo modo Hogar): si LAN está ON y cambia la red (otro Wi-Fi, cable,
    VPN) o Windows pasa a clasificarla como Pública, apaga la LAN automáticamente.
    """
    def _loop():
        counter = 0
        while True:
            time.sleep(interval)
            counter += 1
            try:
                if state.public_mode or not state.lan_enabled:
                    continue
                sig = network_signature()
                base = _baseline["signature"]
                if base is not None and sig != base:
                    # Si el SSID falló momentáneamente y vino None, no considerarlo cambio si la subred IP no cambió
                    if (sig[1] is None or base[1] is None) and sig[0] == base[0]:
                        continue
                    disable_lan("La red cambió: la Red Local se apagó automáticamente por seguridad.")
                    if on_change:
                        on_change("Red Local DESACTIVADA (cambio de red detectado)")
                    continue
                # Cada ~30 s revisar si Windows pasó la red a Pública
                if counter % 6 == 0 and _baseline["category"] != "Public":
                    if get_network_category() == "Public":
                        disable_lan("La red ahora es PÚBLICA: la Red Local se apagó automáticamente por seguridad.")
                        if on_change:
                            on_change("Red Local DESACTIVADA (red pública detectada)")
            except Exception as e:
                print(f"[Seguridad] [!] Error en vigilancia de red: {e!r}")

    threading.Thread(target=_loop, daemon=True).start()
