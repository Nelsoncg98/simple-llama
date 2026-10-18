"""
Sincronizador Universal y Resiliente de Modelos (sync_opencode.py)
Simple Llama
Descubre los modelos en llamacpp/models y sincroniza de forma segura:
  1. OpenCode (~/.config/opencode/opencode.jsonc)
  2. Pi Agent (~/.pi/agent/models.json)

Diseñado con tolerancia total a fallos:
Si un cliente no existe en la máquina, se omite de forma limpia y transparente sin romperse.
"""

import os
import sys
import json

# Asegurar que el directorio de llamacpp esté en sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from config import load_models, PROXY_PORT, atomic_write_json


def strip_jsonc_comments(text: str) -> str:
    """Elimina comentarios // y /* */ respetando cadenas y URLs con comillas."""
    result = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_string:
            result.append(c)
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
        else:
            if c == '"':
                in_string = True
                result.append(c)
                i += 1
            elif c == '/' and i + 1 < n and text[i + 1] == '/':
                i += 2
                while i < n and text[i] != '\n':
                    i += 1
            elif c == '/' and i + 1 < n and text[i + 1] == '*':
                i += 2
                while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                    i += 1
                i += 2
            else:
                result.append(c)
                i += 1
    return "".join(result)


def sync_opencode(models: dict) -> bool:
    """Sincroniza OpenCode. Si no está instalado, se omite sin error."""
    # Buscar ruta en ~/.config o USERPROFILE
    user_home = os.path.expanduser("~")
    candidates = [
        os.path.join(user_home, ".config", "opencode", "opencode.jsonc"),
    ]
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        candidates.append(os.path.join(userprofile, ".config", "opencode", "opencode.jsonc"))

    config_path = None
    for cand in candidates:
        if os.path.exists(cand):
            config_path = cand
            break

    if not config_path:
        print("[i] OpenCode: No detectado en esta máquina (omitido con seguridad).")
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
        clean_content = strip_jsonc_comments(raw_content)
        data = json.loads(clean_content)
    except Exception as e:
        print(f"[!] OpenCode: Error al leer {config_path} ({e}). Omitiendo para proteger el archivo.")
        return False

    if "provider" not in data or not isinstance(data["provider"], dict):
        data["provider"] = {}

    opencode_models = {}
    for p_id, p_cfg in models.items():
        family = p_cfg.get("family", p_id)
        badge = p_cfg.get("badge", f"{p_cfg.get('ctx', 8192) // 1024}k Tokens")
        ctx = int(p_cfg.get("ctx", 8192))
        is_vision = bool(p_cfg.get("vision", False))
        vision_suffix = " [Vision]" if is_vision else ""

        display_name = f"{family} ({badge}){vision_suffix}"
        opencode_models[p_id] = {
            "name": display_name,
            "tool_call": True,
            "reasoning": True,
            "temperature": True,
            "attachment": is_vision,
            "limit": {
                "context": ctx,
                "output": min(8192, ctx // 2)
            },
            "modalities": {
                "input": ["text", "image"] if is_vision else ["text"],
                "output": ["text"]
            }
        }

    current_llamacpp = data["provider"].get("llamacpp", {})
    base_url = current_llamacpp.get("options", {}).get("baseURL", f"http://localhost:{PROXY_PORT}/v1")

    data["provider"]["llamacpp"] = {
        "name": "llama.cpp Local",
        "npm": "@ai-sdk/openai-compatible",
        "options": {
            "baseURL": base_url
        },
        "models": opencode_models
    }

    try:
        # Respaldar archivo original antes de escribir si existe
        if os.path.exists(config_path):
            bak_path = f"{config_path}.bak"
            try:
                with open(config_path, "r", encoding="utf-8") as f_orig:
                    with open(bak_path, "w", encoding="utf-8") as f_bak:
                        f_bak.write(f_orig.read())
            except Exception:
                pass
        atomic_write_json(config_path, data, indent=2, ensure_ascii=False)
        print(f"[+] OpenCode: Sincronizado ({len(opencode_models)} modelos) en {config_path}")
        return True
    except Exception as e:
        print(f"[!] OpenCode: Error al guardar cambios ({e}).")
        return False


def sync_pi(models: dict) -> bool:
    """Sincroniza Pi Agent. Si no está instalado, se omite sin error."""
    user_home = os.path.expanduser("~")
    candidates = [
        os.path.join(user_home, ".pi", "agent"),
    ]
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        candidates.append(os.path.join(userprofile, ".pi", "agent"))

    agent_dir = None
    for cand in candidates:
        if os.path.exists(cand):
            agent_dir = cand
            break

    if not agent_dir:
        print("[i] Pi Agent: No detectado en esta máquina (omitido con seguridad).")
        return False

    models_path = os.path.join(agent_dir, "models.json")
    data = {"providers": {}}

    if os.path.exists(models_path):
        try:
            with open(models_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
            if isinstance(raw_data, dict):
                data = raw_data
        except Exception as e:
            print(f"[!] Pi Agent: Error leyendo {models_path} ({e}). Creando estructura limpia de respaldo.")

    if "providers" not in data or not isinstance(data["providers"], dict):
        data["providers"] = {}

    pi_models = []
    for p_id, p_cfg in models.items():
        ctx = int(p_cfg.get("ctx", 8192))
        is_vision = bool(p_cfg.get("vision", False))
        family = p_cfg.get("family", p_id)
        badge = p_cfg.get("badge", f"{ctx // 1024}k Tokens")
        display_name = f"{family} ({badge})"

        entry = {
            "id": p_id,
            "name": display_name,
            "contextWindow": ctx,
            "maxTokens": min(8192, ctx // 2),
            "input": ["text", "image"] if is_vision else ["text"],
            "reasoning": True
        }
        pi_models.append(entry)

    data["providers"]["llamacpp"] = {
        "api": "openai-completions",
        "apiKey": "llamacpp",
        "baseUrl": f"http://127.0.0.1:{PROXY_PORT}/v1",
        "models": pi_models
    }

    try:
        if os.path.exists(models_path):
            bak_path = f"{models_path}.bak"
            try:
                with open(models_path, "r", encoding="utf-8") as f_orig:
                    with open(bak_path, "w", encoding="utf-8") as f_bak:
                        f_bak.write(f_orig.read())
            except Exception:
                pass
        atomic_write_json(models_path, data, indent=2, ensure_ascii=False)
        print(f"[+] Pi Agent: Sincronizado ({len(pi_models)} modelos) en {models_path}")
        return True
    except Exception as e:
        print(f"[!] Pi Agent: Error al guardar cambios ({e}).")
        return False


def main():
    print("=" * 65)
    print("   SINCRONIZADOR RESILIENTE DE MODELOS LOCALES (OpenCode / Pi)")
    print("=" * 65)

    models = load_models()
    if not models:
        print("[!] No se encontraron modelos ni perfiles en llamacpp/models/.")
        return

    print(f"[*] Modelos descubiertos en GPU: {len(models)}")
    print("-" * 65)

    res_opencode = sync_opencode(models)
    res_pi = sync_pi(models)

    print("-" * 65)
    if res_opencode or res_pi:
        print("[OK] Sincronización finalizada correctamente para los clientes disponibles.")
    else:
        print("[i] No se encontraron clientes instalados para sincronizar.")


if __name__ == "__main__":
    main()
