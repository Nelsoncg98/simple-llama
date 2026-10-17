# ⚡ Simple Llama (llama.cpp)

Un orquestador local ultra-ligero escrito en **Python** para correr modelos de LLM locales de llama.cpp en Windows con aceleración CUDA, pensado para portátiles con **8 GB de RAM** y tarjetas **RTX serie 3000/4000 (4–6 GB VRAM)**.

> **Objetivo central:** ejecutar inference local al menor coste de memoria. Se logra con tres pilares: **hot-swap** seguro entre modelos, un **watchdog** que libera la GPU en reposo, y la **separación de visión del texto puro**.

---

## 📁 Estructura de carpetas

El orquestador se instala dentro del directorio raíz `llamacpp/`. Este directorio es **obligatorio** porque el orquestador lo usa como base para encontrar el binario oficial (`llama-server.exe`) y las carpetas de modelos (`models/`). Sin estos dos elementos el sistema no puede levantar ningún modelo.

```text
llamacpp/                    # Carpeta raíz del proyecto (base obligatoria)
├── orchestrator/            # Carpeta del orquestador (se instala aquí)
│   ├── config.py             # Registro de modelos: rutas, límites de tokens, red y perfiles (fuente de verdad)
│   ├── manager.py            # Gestor de procesos GPU: hot-swap, watchdog y limpieza de memoria
│   ├── proxy.py              # Servidor HTTP ligero + proxy streaming de la API del modelo
│   ├── sync_opencode.py      # Sincronizador resiliente hacia OpenCode / Pi Agent
│   ├── templates/
│   │   └── dashboard.html    # Plantilla visual del panel de control web (se rellena dinámicamente desde config.py)
│   └── __pycache__/          # Compilación intermedia de Python (no es código fuente)
├── models/                   # Carpetas de modelos (cada una con su config.json)
│   ├── gemma/               # Familia Gemma
│   │   └── config.json
│   ├── ornith/              # Familia Ornith 1.5
│   │   └── config.json
│   └── qwen/                # Familia Qwen
├── llama-server.exe         # Binario oficial de llama.cpp (aceleración CUDA)
├── libomp.dll               # Biblioteca OpenMP (requerida por llama-server.exe)
├── LICENSE-LLVM-OpenMP
└── README.md                # Documentación del proyecto
```

### Estructura de `config.json`

Las carpetas de modelos usan el formato **multi-perfil** cuando contienen varios perfiles, o el formato **simple** cuando hay un único perfil. Cada carpeta debe contener su `config.json`.

**Formato multi-perfil** (carpeta con varios perfiles):

```json
{
  "family": "Ornith 1.5 (9B)",
  "reasoning_budget": 8192,
  "profiles": {
    "ornith-low": {
      "model_path": "Ornith-1.5-9B-GSQ-RCO-IQ3_M.gguf",
      "ctx": 65536,
      "desc": "Ornith 1.5 9B - 64k tokens (Caché Q8 puro, ~5.34 GB VRAM)",
      "badge": "64k Tokens",
      "badge_color": "#0284c7",
      "vision": false,
      "temp": 0.60,
      "top_p": 0.95,
      "top_k": 20,
      "presence_penalty": 0.0,
      "repeat_penalty": 1.08,
      "system_prompt": "..."
    },
    "ornith-edge": {
      "model_path": "Ornith-1.5-9B-GSQ-RCO-IQ3_M.gguf",
      "ctx": 73728,
      "desc": "Ornith 1.5 9B - 72k tokens (Caché Q8 puro recomendado, ~5.51 GB VRAM)",
      "badge": "72k Recomendado",
      "badge_color": "#10b981",
      "vision": false,
      "temp": 0.60,
      "top_p": 0.95,
      "top_k": 20,
      "presence_penalty": 0.0,
      "repeat_penalty": 1.08,
      "system_prompt": "..."
    }
  }
}
```

**Formato Simple (Perfil Único):**

Plantilla con todos los parámetros soportados lista para copiar y pegar en `models/<tu-carpeta>/config.json`. Los valores mostrados son los recomendados/por defecto (cualquier parámetro que omitas tomará automáticamente su valor por defecto de la tabla inferior):

```json
{
  "family": "Mi Familia",
  "model_path": "modelo.gguf",
  "ctx": 16384,
  "desc": "Mi modelo - 16k tokens",
  "badge": "16k Tokens",
  "badge_color": "#0284c7",
  "vision": false,
  "projector_path": "mmproj.gguf",
  "cache_type_k": "q8_0",
  "cache_type_v": "q4_0",
  "fa": "on",
  "temp": 0.70,
  "top_p": 0.95,
  "top_k": 40,
  "min_p": 0.05,
  "repeat_penalty": 1.08,
  "presence_penalty": 1.50,
  "reasoning_budget": 8192,
  "system_prompt": "..."
}
```

#### 📋 Tabla de Parámetros y Valores por Defecto

Si omites cualquier clave en tu `config.json`, el sistema asumirá automáticamente estos valores:

| Parámetro | Tipo | Por Defecto (DEFAULT) | Descripción |
| :--- | :---: | :---: | :--- |
| **`family`** | string | *Obligatorio* | Nombre legible de la familia de modelos. |
| **`model_path`** | string | *Obligatorio* | Archivo `.gguf` dentro de la carpeta. |
| **`ctx`** | integer | **`8192`** | Tamaño máximo de contexto en tokens. |
| **`desc`** | string | `"<family> (<ctx>)"` | Descripción en el panel web. |
| **`badge`** | string | `"<ctx/1024>k Tokens"` | Texto de la etiqueta en el dashboard. |
| **`badge_color`** | string | `"#0284c7"` | Color hexadecimal del badge (azul por defecto). |
| **`vision`** | boolean | **`false`** | Visión activa. Si hay `projector_path`, pasa a `true` automático. |
| **`projector_path`**| string | `null` | Archivo `mmproj-*.gguf` de visión (opcional). |
| **`cache_type_k`** | string | **`"q8_0"`** | Cuantización de Keys del KV Cache (`q8_0`, `q4_0`, etc.). |
| **`cache_type_v`** | string | **`"q8_0"`** | Cuantización de Values del KV Cache (`q8_0`, `q4_0`, etc.). |
| **`fa`** | string | **`"on"`** | Flash Attention. `"on"` siempre activo (solo pasa a `"off"` con draft externo). |
| **`temp`** | float | **`0.70`** | Temperatura de muestreo (creatividad/entropía). |
| **`top_p`** | float | **`0.95`** | Nucleus sampling. |
| **`top_k`** | integer | **`40`** | Muestreo Top-K. |
| **`min_p`** | float | `null` *(0.0)* | Mínimo de probabilidad (desactivado si se omite). |
| **`repeat_penalty`**| float | `null` *(1.0)* | Penalización por repetición (1.0 = neutro; 1.08 = anti-bucle). |
| **`presence_penalty`**| float | `null` *(0.0)* | Penalización de presencia (0.0 = neutro; 1.5 = modo general). |
| **`reasoning_budget`**| integer | `null` *(-1)* | Límite máximo de tokens en `<think>` (-1 = sin límite; ej: 8192). |
| **`system_prompt`**| string | `null` | Prompt de sistema inyectado automáticamente. |
| **`chat_template`**| string | `null` | Template Jinja alternativo (omite para usar el del GGUF). |
| **`draft_model`** | string | `null` | Archivo `.gguf` del modelo draft para aceleración especulativa. |
| **`spec_type`** | string | `null` | Tipo de decoding especulativo (ej: `"draft-mtp"`). |

### ¿Por qué se necesita `llamacpp/`?

El orquestador depende de este directorio raíz para funcionar:

1. **`config.py`** escanea `models/` buscando un `config.json` en cada subcarpeta. Sin esta carpeta no hay ningún modelo que descubrir.
2. **`manager.py`** levanta el binario `llama-server.exe`, que vive en la raíz. Sin este binario no hay aceleración GPU.
3. Los scripts `.bat` usan `%~dp0` para posicionarse respecto a su propia carpeta, así el orquestador funciona igual si se mueve de sitio.

---

## 🧠 ¿Qué hace exactamente cada componente?

### 1. `config.py` — Descubrimiento modular de modelos

Es la **fuente de verdad** del sistema. Escanea dinámicamente las carpetas en `models/` buscando un `config.json` en cada una y construye el diccionario global `MODELS`. Soporta dos formatos:

- **Multi-perfil:** `{ "family": "...", "profiles": { "id": { ... } } }`
- **Perfil simple:** `{ "family": "...", "model_path": "...", ... }`

Si una carpeta aún no tiene `config.json`, se omite silenciosamente. Cada perfil expone: `ctx`, `cache_type_k/v`, `vision`, `projector_path`, `draft_model`, `spec_type`, `fa`, y parámetros de generación (`temp`, `top_p`, `top_k`, `min_p`, `repeat_penalty`, `presence_penalty`, `system_prompt`, `chat_template`, `reasoning_budget`).

### 2. `manager.py` — Gestor de procesos GPU

Instancia un único proceso de `llama-server.exe` y lo conmuta entre modelos sin soltar procesos colgados:

- **Hot-swap seguro:** si el modelo correcto ya corre, retorna al instante; si hay otro corriendo, se detiene limpiamente antes de levantar el nuevo.
- **Watchdog (auto-apagado):** un hilo en segundo plano vigila `IDLE_TIMEOUT_SECONDS` (10 min) sin actividad y llama a `stop_server()` para liberar VRAM a ~0 MB. Reconoce actividad directa desde el Chat Web nativo leyendo `/slots`.
- **Optimización de memoria:** `EmptyWorkingSet` del proceso activo para mantener la RAM física en ~40 MB en lugar de ~2.5 GB.

### 3. `proxy.py` — Servidor HTTP + proxy streaming

Un servidor `BaseHTTPRequestHandler` propio que intercepta las peticiones antes de reenviarlas al backend:

- **Listado de modelos** en formato OpenAI (`/v1/models`) y Ollama (`/api/tags`).
- **Dashboard** dinámico renderizado desde la plantilla HTML.
- **Sanitización de mensajes** (`sanitize_messages`): inyecta un system prompt por defecto y normaliza roles `system` para evitar excepciones de Jinja.
- **Proxy streaming:** reenvía peticiones de inferencia a `/v1/chat/completions`, `/completions`, etc., chunk a chunk, preservando el streaming (SSE). Marca la etiqueta de cliente y calcula tokens/velocidad para benchmarks.
- **Endpoints manuales** del panel web: `POST /load` y `POST /unload` para lanzar o liberar GPU.

### 4. `sync_opencode.py` — Sincronizador resiliente

Pega los modelos descubiertos en la config de otros clientes sin romperse si no están instalados:

- **OpenCode:** lee/escribe `~/.config/opencode/opencode.jsonc`.
- **Pi Agent:** lee/escribe `~/.pi/agent/models.json`.

---

## 🚀 ¿Por qué este orquestador y no Ollama tradicional?

| En Ollama estándar | Solución aquí |
|---|---|
| Consume 2.3–3.0 GB de RAM física de 8 GB | **< 40 MB** (limpieza `EmptyWorkingSet`) |
| Proyectores de visión obligatorios ~1 GB aunque solo uses texto | Visión desacoplada: perfiles de texto puro ahorran ~1 GB de VRAM |
| Contexto sin optimizar desborda a memoria compartida y congela la PC | **Flash Attention (`-fa on`)** + KV Cache Q8_0, todo en GPU |
| Si no lo usas, sigue ocupando VRAM | **Watchdog** tras 10 min de inactividad apaga la GPU a 0 MB |
| Cambiar de modelo es lento o deja procesos colgados | **Hot-swap** detiene el modelo previo limpiamente antes del nuevo |

---

## ▶️ Cómo ejecutarlo

### Viene con `iniciar-servidor.bat` (opción A)

```bat
@echo off
title Mini-Orquestador IA - llama.cpp
cd /d "%~dp0\orchestrator"
python proxy.py
pause
```

- Sirve el dashboard en **11435**, la API en `:11435/v1` y el backend nativo en `:11436`.

### Sincronizar con OpenCode o Pi Agent (opción B)

El archivo `configopencode.bat` se mueve a la carpeta `orchestrator/` para que se pueda sincronizar también.

```bat
@echo off
chcp 65001 >nul
title Sincronizar Modelos con OpenCode
cls
cd /d "%~dp0\orchestrator"
if exist "sync_opencode.py" ( python sync_opencode.py ) else ( echo [!] No se encontró sync_opencode.py. )
pause
```

- Pega los modelos descubiertos en `~/.config/opencode/opencode.jsonc` y `~/.pi/agent/models.json`.

---

## ⚠️ Buenas prácticas para 6 GB de VRAM

1. **Evita nombres reservados** en los perfiles: `server`, `load`, `unload`, `health`, `props`. Usa siempre prefijos descriptivos (p. ej. `qwen-edge`, `ornith-edge`).
2. **Cálculo de VRAM segura:**
   - Modelo 4B → ~3.5 GB en VRAM.
   - Modelo 7B / 9B → ~4.2–4.8 GB; empieza probando con **16k tokens (16,384)**.
3. **Visión multimodal:** si configuras `"vision": true`, define `projector_path` apuntando al archivo `.gguf` del proyector `mmproj`.
