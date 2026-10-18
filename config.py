"""
Configuración de Simple Llama (llama.cpp)
Descubrimiento modular automático mediante carpetas en models/
"""

import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Directorio raíz de llama.cpp (carpeta contenedora donde residen los binarios y models/)
if os.path.exists(os.path.join(os.path.dirname(BASE_DIR), "llama-server.exe")):
    LLAMACPP_DIR = os.path.dirname(BASE_DIR)
else:
    LLAMACPP_DIR = BASE_DIR

LLAMA_SERVER_BIN = os.path.join(LLAMACPP_DIR, "llama-server.exe")
MODELS_DIR = os.path.join(LLAMACPP_DIR, "models")

# Configuración de Red
PROXY_HOST = "0.0.0.0"
PROXY_PORT = 11435
BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 11436

# Configuración persistente (settings.json)
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

def load_settings():
    """Carga la configuración persistente global."""
    default_settings = {"lan_access": False, "idle_timeout_minutes": 10}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    default_settings.update(data)
                    return default_settings
        except Exception:
            pass
    return default_settings

def save_settings(data):
    """Guarda la configuración persistente global."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def is_lan_allowed():
    return bool(load_settings().get("lan_access", False))

def get_idle_timeout_minutes():
    """Retorna los minutos de inactividad antes de auto-apagar (0 = Desactivado/Nunca)."""
    return int(load_settings().get("idle_timeout_minutes", 10))

def get_idle_timeout_seconds():
    """Retorna los segundos de inactividad (0 = Desactivado/Nunca)."""
    mins = get_idle_timeout_minutes()
    return max(0, mins * 60)

def set_idle_timeout(minutes):
    """Actualiza el tiempo de auto-apagado."""
    cfg = load_settings()
    cfg["idle_timeout_minutes"] = max(0, int(minutes))
    save_settings(cfg)

def get_local_ip():
    """Obtiene la IP local de la interfaz de red activa."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Tiempo de inactividad antes de auto-apagar el modelo y liberar VRAM (en segundos)
IDLE_TIMEOUT_SECONDS = get_idle_timeout_seconds()

# Valores de respaldo seguros si no se especifican en config.json
DEFAULT_CTX = 8192          # 8k tokens (arranque seguro universal para cualquier modelo)
DEFAULT_CACHE_TYPE = "q8_0" # Estándar de oro de alta fidelidad para KV Cache


def _resolve_path(base_folder, path_str):
    """Resuelve la ruta si es relativa a la carpeta del modelo o mantiene la absoluta."""
    if not path_str:
        return None
    if os.path.isabs(path_str):
        return path_str
    return os.path.abspath(os.path.join(base_folder, path_str))


def load_models():
    """
    Escanea dinámicamente cada subcarpeta en 'models/' buscando 'config.json'.
    Soporta:
      1. Formato multi-perfil: { "family": "...", "profiles": { "id": { ... } } }
      2. Formato simple de perfil único: { "family": "...", "model_path": "...", ... }
    Si una carpeta aún no tiene config.json (ej. modelo recién descargado), se omite silenciosamente sin romper nada.
    """
    discovered = {}
    if not os.path.exists(MODELS_DIR):
        return discovered

    for entry in os.scandir(MODELS_DIR):
        if not entry.is_dir():
            continue

        config_path = os.path.join(entry.path, "config.json")
        if not os.path.exists(config_path):
            continue

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Config] [!] Advertencia: Error leyendo {config_path}: {e}")
            continue

        default_family = data.get("family", entry.name.capitalize())

        # Caso 1: Archivo con múltiples perfiles ("profiles": { ... })
        if "profiles" in data and isinstance(data["profiles"], dict):
            for p_id, p_cfg in data["profiles"].items():
                model_file = p_cfg.get("model_path") or p_cfg.get("model_file")
                if not model_file:
                    continue

                full_model_path = _resolve_path(entry.path, model_file)
                projector_file = p_cfg.get("projector_path") or p_cfg.get("projector_file")
                full_proj_path = _resolve_path(entry.path, projector_file) if projector_file else None

                ctx = int(p_cfg.get("ctx", DEFAULT_CTX))
                cache_type = p_cfg.get("cache_type", DEFAULT_CACHE_TYPE)
                cache_type_k = p_cfg.get("cache_type_k", cache_type)
                cache_type_v = p_cfg.get("cache_type_v", cache_type)

                draft_file = p_cfg.get("draft_model") or p_cfg.get("draft_path")
                full_draft_path = _resolve_path(entry.path, draft_file) if draft_file else None

                discovered[p_id] = {
                    "family": p_cfg.get("family", default_family),
                    "model_path": full_model_path,
                    "projector_path": full_proj_path,
                    "draft_path": full_draft_path,
                    "spec_type": p_cfg.get("spec_type", "draft-mtp" if full_draft_path else None),
                    "spec_draft_n_max": p_cfg.get("spec_draft_n_max", None),
                    "fa": p_cfg.get("fa", None),
                    "ctx": ctx,
                    "desc": p_cfg.get("desc", f"{default_family} ({ctx} tokens)"),
                    "badge": p_cfg.get("badge", f"{ctx // 1024}k Tokens"),
                    "badge_color": p_cfg.get("badge_color", "#0284c7"),
                    "vision": bool(p_cfg.get("vision", False) or full_proj_path is not None),
                    "cache_type_k": cache_type_k,
                    "cache_type_v": cache_type_v,
                    "temp": float(p_cfg.get("temp", 0.70)),
                    "top_p": float(p_cfg.get("top_p", 0.95)),
                    "top_k": int(p_cfg.get("top_k", 40)),
                    "min_p": float(p_cfg["min_p"]) if "min_p" in p_cfg else None,
                    "repeat_penalty": float(p_cfg["repeat_penalty"]) if "repeat_penalty" in p_cfg else None,
                    "presence_penalty": float(p_cfg["presence_penalty"]) if "presence_penalty" in p_cfg else None,
                    "system_prompt": p_cfg.get("system_prompt", data.get("system_prompt")),
                    "chat_template": p_cfg.get("chat_template", data.get("chat_template", None)),
                    "reasoning_budget": int(p_cfg["reasoning_budget"]) if "reasoning_budget" in p_cfg else (int(data["reasoning_budget"]) if "reasoning_budget" in data else None),
                    "_config_path": config_path,
                    "_folder_path": entry.path,
                    "_folder_name": entry.name,
                    "_profile_key": p_id,
                    "_model_file": model_file,
                    "_projector_file": projector_file,
                    "_draft_file": draft_file,
                }

        # Caso 2: Perfil individual simple a nivel raíz
        elif "model_path" in data or "model_file" in data:
            p_id = data.get("name", entry.name)
            model_file = data.get("model_path") or data.get("model_file")
            full_model_path = _resolve_path(entry.path, model_file)
            projector_file = data.get("projector_path") or data.get("projector_file")
            full_proj_path = _resolve_path(entry.path, projector_file) if projector_file else None

            cache_type = data.get("cache_type", DEFAULT_CACHE_TYPE)
            cache_type_k = data.get("cache_type_k", cache_type)
            cache_type_v = data.get("cache_type_v", cache_type)

            ctx = int(data.get("ctx", DEFAULT_CTX))
            discovered[p_id] = {
                "family": default_family,
                "model_path": full_model_path,
                "projector_path": full_proj_path,
                "ctx": ctx,
                "desc": data.get("desc", f"{default_family} ({ctx} tokens)"),
                "badge": data.get("badge", f"{ctx // 1024}k Tokens"),
                "badge_color": data.get("badge_color", "#0284c7"),
                "vision": bool(data.get("vision", False) or full_proj_path is not None),
                "cache_type_k": cache_type_k,
                "cache_type_v": cache_type_v,
                "temp": float(data.get("temp", 0.70)),
                "top_p": float(data.get("top_p", 0.95)),
                "top_k": int(data.get("top_k", 40)),
                "min_p": float(data["min_p"]) if "min_p" in data else None,
                "repeat_penalty": float(data["repeat_penalty"]) if "repeat_penalty" in data else None,
                "presence_penalty": float(data["presence_penalty"]) if "presence_penalty" in data else None,
                "system_prompt": data.get("system_prompt"),
                "reasoning_budget": int(data["reasoning_budget"]) if "reasoning_budget" in data else None,
                "_config_path": config_path,
                "_folder_path": entry.path,
                "_folder_name": entry.name,
                "_profile_key": None,
                "_model_file": model_file,
                "_projector_file": projector_file,
                "_draft_file": None,
            }

    return discovered


MODELS = load_models()


def reload_models():
    """Recarga los modelos en caliente actualizando el diccionario en memoria."""
    global DEFAULT_MODEL
    updated = load_models()
    MODELS.clear()
    MODELS.update(updated)
    if "gemma4b-edge" in MODELS:
        DEFAULT_MODEL = "gemma4b-edge"
    elif MODELS:
        DEFAULT_MODEL = next(iter(MODELS))
    return MODELS


DEFAULT_MODEL = "gemma4b-edge" if "gemma4b-edge" in MODELS else (next(iter(MODELS)) if MODELS else "gemma4b-edge")


def get_model_details(model_id):
    """
    Retorna la configuración completa del modelo y los archivos .gguf disponibles
    en su carpeta para permitir edición visual sin tocar código.
    """
    if model_id not in MODELS:
        reload_models()
    if model_id not in MODELS:
        return None

    cfg = MODELS[model_id]
    folder_path = cfg.get("_folder_path")
    available_gguf = []
    if folder_path and os.path.exists(folder_path):
        for fname in os.listdir(folder_path):
            if fname.lower().endswith(".gguf"):
                available_gguf.append(fname)
        available_gguf.sort()

    return {
        "id": model_id,
        "family": cfg.get("family", ""),
        "folder_name": cfg.get("_folder_name", ""),
        "model_file": cfg.get("_model_file", os.path.basename(cfg["model_path"])),
        "projector_file": cfg.get("_projector_file", "") or "",
        "draft_file": cfg.get("_draft_file", "") or "",
        "ctx": cfg.get("ctx", DEFAULT_CTX),
        "desc": cfg.get("desc", ""),
        "badge": cfg.get("badge", ""),
        "badge_color": cfg.get("badge_color", "#38bdf8"),
        "vision": cfg.get("vision", False),
        "fa": cfg.get("fa", "on"),
        "cache_type_k": cfg.get("cache_type_k", "q8_0"),
        "cache_type_v": cfg.get("cache_type_v", "q8_0"),
        "temp": cfg.get("temp", 0.70),
        "top_p": cfg.get("top_p", 0.95),
        "top_k": cfg.get("top_k", 40),
        "min_p": cfg.get("min_p"),
        "repeat_penalty": cfg.get("repeat_penalty"),
        "presence_penalty": cfg.get("presence_penalty"),
        "system_prompt": cfg.get("system_prompt") or "",
        "available_gguf": available_gguf,
    }


def save_model_profile(model_id, payload):
    """
    Guarda las modificaciones de un perfil en su archivo config.json correspondiente
    preservando el formato y recargando el catálogo en caliente.
    """
    if model_id not in MODELS:
        reload_models()
    if model_id not in MODELS:
        return False, f"Modelo '{model_id}' no encontrado"

    cfg = MODELS[model_id]
    config_path = cfg.get("_config_path")
    if not config_path or not os.path.exists(config_path):
        return False, f"No se encontró el archivo config.json para '{model_id}'"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        return False, f"Error leyendo {config_path}: {e}"

    profile_key = cfg.get("_profile_key")
    target_dict = None

    if profile_key and "profiles" in raw_data and profile_key in raw_data["profiles"]:
        target_dict = raw_data["profiles"][profile_key]
    elif not profile_key:
        target_dict = raw_data
    else:
        return False, f"No se encontró el perfil '{profile_key}' en el archivo de configuración"

    # Actualizar campos editables de manera controlada y tipada
    if "model_file" in payload and payload["model_file"]:
        target_dict["model_path"] = str(payload["model_file"]).strip()
    if "ctx" in payload:
        try:
            target_dict["ctx"] = int(payload["ctx"])
        except (ValueError, TypeError):
            pass
    if "desc" in payload:
        target_dict["desc"] = str(payload["desc"]).strip()
    if "badge" in payload:
        target_dict["badge"] = str(payload["badge"]).strip()
    if "badge_color" in payload and payload["badge_color"]:
        target_dict["badge_color"] = str(payload["badge_color"]).strip()
    if "temp" in payload:
        try:
            target_dict["temp"] = float(payload["temp"])
        except (ValueError, TypeError):
            pass
    if "top_p" in payload:
        try:
            target_dict["top_p"] = float(payload["top_p"])
        except (ValueError, TypeError):
            pass
    if "top_k" in payload:
        try:
            target_dict["top_k"] = int(payload["top_k"])
        except (ValueError, TypeError):
            pass
    if "min_p" in payload:
        try:
            target_dict["min_p"] = float(payload["min_p"]) if payload["min_p"] not in ("", None) else None
        except (ValueError, TypeError):
            pass
    if "repeat_penalty" in payload:
        try:
            target_dict["repeat_penalty"] = float(payload["repeat_penalty"]) if payload["repeat_penalty"] not in ("", None) else None
        except (ValueError, TypeError):
            pass
    if "presence_penalty" in payload:
        try:
            target_dict["presence_penalty"] = float(payload["presence_penalty"]) if payload["presence_penalty"] not in ("", None) else None
        except (ValueError, TypeError):
            pass
    if "fa" in payload:
        fa_val = str(payload["fa"]).strip().lower()
        target_dict["fa"] = "on" if fa_val in ("on", "true", "1") else "off"
    if "cache_type_k" in payload and payload["cache_type_k"]:
        target_dict["cache_type_k"] = str(payload["cache_type_k"]).strip()
    if "cache_type_v" in payload and payload["cache_type_v"]:
        target_dict["cache_type_v"] = str(payload["cache_type_v"]).strip()
    if "vision" in payload:
        target_dict["vision"] = bool(payload["vision"])
    if "projector_file" in payload:
        proj = str(payload["projector_file"]).strip()
        if proj:
            target_dict["projector_path"] = proj
        elif "projector_path" in target_dict:
            del target_dict["projector_path"]
    if "draft_file" in payload:
        draft = str(payload["draft_file"]).strip()
        if draft:
            target_dict["draft_model"] = draft
        elif "draft_model" in target_dict:
            del target_dict["draft_model"]
    if "system_prompt" in payload:
        sp = str(payload["system_prompt"]).strip()
        if sp:
            target_dict["system_prompt"] = sp
        elif "system_prompt" in target_dict:
            del target_dict["system_prompt"]

    # Limpiar campos con valor None para mantener el JSON limpio
    keys_to_clean = [k for k, v in target_dict.items() if v is None]
    for k in keys_to_clean:
        del target_dict[k]

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        reload_models()
        return True, "Configuración guardada correctamente"
    except Exception as e:
        return False, f"Error escribiendo {config_path}: {e}"


def get_available_folders():
    """Retorna las carpetas en models/ con sus archivos .gguf para crear nuevos perfiles."""
    folders = {}
    if not os.path.exists(MODELS_DIR):
        return folders

    for entry in os.scandir(MODELS_DIR):
        if not entry.is_dir():
            continue
        ggufs = [f for f in os.listdir(entry.path) if f.lower().endswith(".gguf")]
        if ggufs:
            config_path = os.path.join(entry.path, "config.json")
            family = entry.name.capitalize()
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        family = data.get("family", family)
                except Exception:
                    pass
            folders[entry.name] = {
                "folder": entry.name,
                "family": family,
                "gguf_files": sorted(ggufs),
            }
    return folders


def create_model_profile(folder_name, profile_id, payload):
    """Crea un nuevo perfil dentro de la carpeta especificada en models/."""
    profile_id = str(profile_id).strip().lower().replace(" ", "-")
    if not profile_id:
        return False, "El ID del perfil no puede estar vacío"

    folder_path = os.path.join(MODELS_DIR, folder_name)
    if not os.path.exists(folder_path):
        return False, f"Carpeta '{folder_name}' no existe"

    config_path = os.path.join(folder_path, "config.json")
    raw_data = {"family": folder_name.capitalize(), "profiles": {}}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw_data = loaded
        except Exception as e:
            return False, f"Error leyendo {config_path}: {e}"

    if "profiles" not in raw_data or not isinstance(raw_data["profiles"], dict):
        raw_data["profiles"] = {}

    if profile_id in raw_data["profiles"]:
        return False, f"El ID '{profile_id}' ya existe en esta familia"

    new_profile = {
        "model_path": payload.get("model_file", ""),
        "ctx": int(payload.get("ctx", DEFAULT_CTX)),
        "desc": str(payload.get("desc", f"{profile_id} perfil personalizado")),
        "badge": str(payload.get("badge", "Custom")),
        "badge_color": str(payload.get("badge_color", "#38bdf8")),
        "temp": float(payload.get("temp", 0.70)),
        "top_p": float(payload.get("top_p", 0.95)),
        "top_k": int(payload.get("top_k", 40)),
        "cache_type_k": str(payload.get("cache_type_k", "q8_0")),
        "cache_type_v": str(payload.get("cache_type_v", "q8_0")),
        "fa": "on" if str(payload.get("fa", "on")).lower() in ("on", "true", "1") else "off",
    }
    if payload.get("projector_file"):
        new_profile["projector_path"] = str(payload["projector_file"]).strip()
        new_profile["vision"] = True
    if payload.get("system_prompt"):
        new_profile["system_prompt"] = str(payload["system_prompt"]).strip()

    raw_data["profiles"][profile_id] = new_profile

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        reload_models()
        return True, "Perfil creado correctamente"
    except Exception as e:
        return False, f"Error creando perfil: {e}"
