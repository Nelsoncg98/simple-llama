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

# Directorio de modelos: 100% aislado y exclusivo dentro del orquestador
MODELS_DIR = os.path.join(BASE_DIR, "models")

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


CENTRAL_CONFIG_PATH = os.path.join(MODELS_DIR, "config.json")

def ensure_central_config():
    """Garantiza que exista la carpeta models/ y dentro de models/ el config.json vacío."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    if not os.path.exists(CENTRAL_CONFIG_PATH):
        try:
            with open(CENTRAL_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"models": {}}, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

ensure_central_config()


def get_search_directories():
    """Retorna los directorios de búsqueda de modelos (exclusivamente interno del orquestador)."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    return [MODELS_DIR]


def find_model_file(filename_or_path):
    """
    Localiza un archivo .gguf exclusivamente dentro de orchestrator/models/.
    No permite rutas externas fuera del directorio local.
    """
    if not filename_or_path:
        return None

    fname = os.path.basename(filename_or_path)

    # Si es ruta absoluta y está dentro de MODELS_DIR y existe, aceptarla
    if os.path.isabs(filename_or_path):
        norm_target = os.path.normpath(filename_or_path)
        norm_models = os.path.normpath(MODELS_DIR)
        if norm_target.startswith(norm_models) and os.path.exists(norm_target):
            return norm_target

    # Buscar exclusivamente en MODELS_DIR (y sus subcarpetas si las hubiera)
    direct_try = os.path.join(MODELS_DIR, fname)
    if os.path.exists(direct_try):
        return os.path.abspath(direct_try)

    for root, _, files in os.walk(MODELS_DIR):
        if fname in files:
            return os.path.abspath(os.path.join(root, fname))

    return None


def scan_all_available_gguf():
    """
    Escanea recursivamente todos los archivos .gguf disponibles en el sistema y los clasifica
    amigablemente en modelos ejecutables, adaptadores de visión (mmproj) y aceleradores (draft/mtp).
    """
    models = []
    projectors = []
    drafts = []
    all_files = []

    seen = set()
    for s_dir in get_search_directories():
        for root, _, files in os.walk(s_dir):
            for f in files:
                if f.lower().endswith(".gguf") and f not in seen:
                    seen.add(f)
                    full_p = os.path.join(root, f)
                    try:
                        size_gb = round(os.path.getsize(full_p) / (1024**3), 2)
                    except Exception:
                        size_gb = 0

                    item = {
                        "filename": f,
                        "path": full_p,
                        "size_gb": size_gb,
                    }
                    all_files.append(item)

                    f_lower = f.lower()
                    if "mmproj" in f_lower:
                        projectors.append(item)
                    elif "draft" in f_lower or "mtp" in f_lower:
                        drafts.append(item)
                    else:
                        models.append(item)

    models.sort(key=lambda x: x["filename"].lower())
    projectors.sort(key=lambda x: x["filename"].lower())
    drafts.sort(key=lambda x: x["filename"].lower())
    all_files.sort(key=lambda x: x["filename"].lower())

    return {
        "models": models,
        "projectors": projectors,
        "drafts": drafts,
        "all": all_files,
    }


def load_models():
    """
    Carga todos los perfiles de modelos desde el archivo único y central 'config.json'.
    Resuelve automáticamente las rutas de archivos en disco.
    """
    discovered = {}
    if not os.path.exists(CENTRAL_CONFIG_PATH):
        return discovered

    try:
        with open(CENTRAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Config] [!] Error leyendo {CENTRAL_CONFIG_PATH}: {e}")
        return discovered

    models_dict = data.get("models", {})
    if not isinstance(models_dict, dict):
        return discovered

    for p_id, p_cfg in models_dict.items():
        if not isinstance(p_cfg, dict):
            continue

        raw_model_path = p_cfg.get("model_path")
        full_model_path = find_model_file(raw_model_path)
        file_exists = bool(full_model_path and os.path.exists(full_model_path))

        proj_file = p_cfg.get("projector_path")
        full_proj = find_model_file(proj_file) if proj_file else None

        draft_file = p_cfg.get("draft_path") or p_cfg.get("draft_model")
        full_draft = find_model_file(draft_file) if draft_file else None

        ctx = int(p_cfg.get("ctx", DEFAULT_CTX))
        family = p_cfg.get("family", "Modelos Generales")

        discovered[p_id] = {
            "family": family,
            "model_path": full_model_path or raw_model_path,
            "model_file": os.path.basename(raw_model_path) if raw_model_path else "",
            "projector_path": full_proj,
            "projector_file": os.path.basename(proj_file) if proj_file else "",
            "draft_path": full_draft,
            "draft_file": os.path.basename(draft_file) if draft_file else "",
            "spec_type": p_cfg.get("spec_type", "draft-mtp" if full_draft else None),
            "spec_draft_n_max": p_cfg.get("spec_draft_n_max", None),
            "fa": p_cfg.get("fa", "on"),
            "ctx": ctx,
            "desc": p_cfg.get("desc", f"{family} ({ctx} tokens)"),
            "badge": p_cfg.get("badge", f"{ctx // 1024}k Tokens"),
            "badge_color": p_cfg.get("badge_color", "#38bdf8"),
            "vision": bool(p_cfg.get("vision", False) or full_proj is not None),
            "cache_type_k": p_cfg.get("cache_type_k", "q8_0"),
            "cache_type_v": p_cfg.get("cache_type_v", "q8_0"),
            "temp": float(p_cfg.get("temp", 0.70)),
            "top_p": float(p_cfg.get("top_p", 0.95)),
            "top_k": int(p_cfg.get("top_k", 40)),
            "min_p": float(p_cfg["min_p"]) if "min_p" in p_cfg and p_cfg["min_p"] is not None else None,
            "repeat_penalty": float(p_cfg["repeat_penalty"]) if "repeat_penalty" in p_cfg and p_cfg["repeat_penalty"] is not None else None,
            "presence_penalty": float(p_cfg["presence_penalty"]) if "presence_penalty" in p_cfg and p_cfg["presence_penalty"] is not None else None,
            "system_prompt": p_cfg.get("system_prompt"),
            "chat_template": p_cfg.get("chat_template"),
            "reasoning_budget": int(p_cfg["reasoning_budget"]) if "reasoning_budget" in p_cfg and p_cfg["reasoning_budget"] is not None else None,
            "file_exists": file_exists,
        }

    return discovered


MODELS = load_models()


def reload_models():
    """Recarga los modelos en caliente actualizando el diccionario en memoria."""
    global DEFAULT_MODEL
    updated = load_models()
    MODELS.clear()
    MODELS.update(updated)
    if "gemma12b-edge" in MODELS:
        DEFAULT_MODEL = "gemma12b-edge"
    elif MODELS:
        DEFAULT_MODEL = next(iter(MODELS))
    return MODELS


DEFAULT_MODEL = "gemma12b-edge" if "gemma12b-edge" in MODELS else (next(iter(MODELS)) if MODELS else "gemma12b-edge")


def get_model_details(model_id):
    """
    Retorna la configuración completa del modelo y los archivos .gguf disponibles
    en todo el sistema clasificados para facilitar su edición.
    """
    if model_id not in MODELS:
        reload_models()
    if model_id not in MODELS:
        return None

    cfg = MODELS[model_id]
    catalog = scan_all_available_gguf()

    return {
        "id": model_id,
        "family": cfg.get("family", ""),
        "model_file": cfg.get("model_file", os.path.basename(cfg["model_path"])),
        "projector_file": cfg.get("projector_file", "") or "",
        "draft_file": "__embedded__" if (cfg.get("spec_type") and not cfg.get("draft_file")) else (cfg.get("draft_file", "") or ""),
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
        "reasoning_budget": cfg.get("reasoning_budget"),
        "system_prompt": cfg.get("system_prompt") or "",
        "file_exists": cfg.get("file_exists", True),
        "catalog": catalog,
    }


def save_model_profile(model_id, payload):
    """
    Guarda las modificaciones de un perfil en el archivo único central config.json
    preservando el formato y recargando el catálogo en caliente.
    """
    if not os.path.exists(CENTRAL_CONFIG_PATH):
        return False, "No se encontró config.json"

    try:
        with open(CENTRAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        return False, f"Error leyendo {CENTRAL_CONFIG_PATH}: {e}"

    if "models" not in raw_data:
        raw_data["models"] = {}

    if model_id not in raw_data["models"]:
        return False, f"Modelo '{model_id}' no encontrado en config.json"

    target_dict = raw_data["models"][model_id]

    if "family" in payload and payload["family"]:
        target_dict["family"] = str(payload["family"]).strip()
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
    if "projector_file" in payload:
        proj = str(payload["projector_file"]).strip()
        if proj:
            target_dict["projector_path"] = proj
            target_dict["vision"] = True
        else:
            target_dict["projector_path"] = None
            target_dict["vision"] = False
    if "draft_file" in payload:
        draft = str(payload["draft_file"]).strip()
        if draft == "__embedded__":
            target_dict["draft_path"] = None
            target_dict["spec_type"] = "draft-mtp"
        elif draft:
            target_dict["draft_path"] = draft
            target_dict["spec_type"] = "draft-mtp"
        else:
            target_dict["draft_path"] = None
            target_dict["spec_type"] = None
    if "reasoning_budget" in payload:
        try:
            target_dict["reasoning_budget"] = int(payload["reasoning_budget"]) if payload["reasoning_budget"] not in ("", None) else None
        except (ValueError, TypeError):
            pass
    if "system_prompt" in payload:
        sp = str(payload["system_prompt"]).strip()
        target_dict["system_prompt"] = sp if sp else None

    # Limpiar None
    keys_to_clean = [k for k, v in target_dict.items() if v is None]
    for k in keys_to_clean:
        del target_dict[k]

    # Renombrado seguro de perfil si cambió el ID
    raw_new_id = payload.get("new_id") or payload.get("id")
    final_id = model_id
    if raw_new_id:
        import re
        clean_new_id = str(raw_new_id).strip().lower()
        clean_new_id = re.sub(r'[\s_]+', '-', clean_new_id)
        clean_new_id = re.sub(r'[^a-z0-9\-\.\+]', '', clean_new_id)

        if clean_new_id and clean_new_id != model_id:
            if clean_new_id in raw_data["models"]:
                return False, f"El ID '{clean_new_id}' ya existe en el catálogo"

            # Reconstruir el diccionario conservando exactamente el mismo orden de los perfiles
            ordered_models = {}
            for k, v in raw_data["models"].items():
                if k == model_id:
                    ordered_models[clean_new_id] = target_dict
                else:
                    ordered_models[k] = v
            raw_data["models"] = ordered_models
            final_id = clean_new_id

    try:
        with open(CENTRAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        reload_models()
        return True, f"Perfil '{final_id}' guardado correctamente"
    except Exception as e:
        return False, f"Error escribiendo config.json: {e}"


def create_model_profile(profile_id, payload):
    """
    Crea un nuevo perfil en el archivo único central config.json sin depender de subcarpetas.
    """
    profile_id = str(profile_id).strip().lower().replace(" ", "-")
    if not profile_id:
        return False, "El ID del perfil no puede estar vacío"

    if not os.path.exists(CENTRAL_CONFIG_PATH):
        raw_data = {"models": {}}
    else:
        try:
            with open(CENTRAL_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as e:
            return False, f"Error leyendo config.json: {e}"

    if "models" not in raw_data or not isinstance(raw_data["models"], dict):
        raw_data["models"] = {}

    if profile_id in raw_data["models"]:
        return False, f"El ID '{profile_id}' ya existe en el catálogo"

    model_file = str(payload.get("model_file", "")).strip()
    if not model_file:
        return False, "Debes seleccionar un archivo de modelo (.gguf)"

    family_name = str(payload.get("family", "")).strip() or "General"
    ctx_val = int(payload.get("ctx", DEFAULT_CTX))

    new_profile = {
        "family": family_name,
        "model_path": model_file,
        "ctx": ctx_val,
        "desc": str(payload.get("desc", f"{family_name} ({ctx_val} tokens)")),
        "badge": str(payload.get("badge", f"{ctx_val // 1024}k Tokens")),
        "badge_color": str(payload.get("badge_color", "#38bdf8")),
        "temp": float(payload.get("temp", 0.70)),
        "cache_type_k": str(payload.get("cache_type_k", "q8_0")),
        "cache_type_v": str(payload.get("cache_type_v", "q8_0")),
        "fa": "on" if str(payload.get("fa", "on")).lower() in ("on", "true", "1") else "off",
    }

    if "top_p" in payload and payload["top_p"] not in ("", None):
        try:
            new_profile["top_p"] = float(payload["top_p"])
        except (ValueError, TypeError):
            pass
    if "top_k" in payload and payload["top_k"] not in ("", None):
        try:
            new_profile["top_k"] = int(payload["top_k"])
        except (ValueError, TypeError):
            pass
    if "min_p" in payload and payload["min_p"] not in ("", None):
        try:
            new_profile["min_p"] = float(payload["min_p"])
        except (ValueError, TypeError):
            pass
    if "repeat_penalty" in payload and payload["repeat_penalty"] not in ("", None):
        try:
            new_profile["repeat_penalty"] = float(payload["repeat_penalty"])
        except (ValueError, TypeError):
            pass
    if "presence_penalty" in payload and payload["presence_penalty"] not in ("", None):
        try:
            new_profile["presence_penalty"] = float(payload["presence_penalty"])
        except (ValueError, TypeError):
            pass
    if "reasoning_budget" in payload and payload["reasoning_budget"] not in ("", None):
        try:
            new_profile["reasoning_budget"] = int(payload["reasoning_budget"])
        except (ValueError, TypeError):
            pass

    if payload.get("projector_file"):
        new_profile["projector_path"] = str(payload["projector_file"]).strip()
        new_profile["vision"] = True
    if payload.get("draft_file"):
        df = str(payload["draft_file"]).strip()
        if df == "__embedded__":
            new_profile["draft_path"] = None
            new_profile["spec_type"] = "draft-mtp"
        elif df:
            new_profile["draft_path"] = df
            new_profile["spec_type"] = "draft-mtp"
    if payload.get("system_prompt"):
        new_profile["system_prompt"] = str(payload["system_prompt"]).strip()

    raw_data["models"][profile_id] = new_profile

    try:
        with open(CENTRAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        reload_models()
        return True, "Perfil creado correctamente"
    except Exception as e:
        return False, f"Error creando perfil: {e}"


def delete_model_profile(profile_id):
    """
    Elimina un perfil de modelo del archivo único central config.json
    sin tocar ningún archivo de peso .gguf en disco.
    """
    profile_id = str(profile_id).strip()
    if not os.path.exists(CENTRAL_CONFIG_PATH):
        return False, "No se encontró config.json"

    try:
        with open(CENTRAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except Exception as e:
        return False, f"Error leyendo config.json: {e}"

    if "models" not in raw_data or profile_id not in raw_data["models"]:
        return False, f"El perfil '{profile_id}' no existe en config.json"

    del raw_data["models"][profile_id]

    try:
        with open(CENTRAL_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False)
        reload_models()
        return True, f"Perfil '{profile_id}' eliminado correctamente"
    except Exception as e:
        return False, f"Error al guardar config.json tras eliminar: {e}"
