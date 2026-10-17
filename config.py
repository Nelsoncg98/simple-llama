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

# Tiempo de inactividad antes de auto-apagar el modelo y liberar VRAM (en segundos)
IDLE_TIMEOUT_SECONDS = 600  # 10 minutos

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
