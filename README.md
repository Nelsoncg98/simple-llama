# ⚡ Simple Llama

> **Orquestador local ultra-ligero para [llama.cpp](https://github.com/ggerganov/llama.cpp)**. Diseñado específicamente para portátiles y PCs con recursos moderados (**8 GB de RAM y GPUs RTX de 4–6 GB VRAM**).

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![llama.cpp](https://img.shields.io/badge/backend-llama.cpp%20CUDA-green)](https://github.com/ggerganov/llama.cpp)
[![Dependencies](https://img.shields.io/badge/dependencies-0%20(Pure%20Stdlib)-success)](#-cero-dependencias-externas)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey)](#)
[![API](https://img.shields.io/badge/API-OpenAI%20%26%20Ollama%20Compatible-blue)](#)

<div align="center">
  <img src="assets/dashboard.png" alt="Simple Llama Web Dashboard" width="100%"/>
</div>

---

## 🌟 ¿Qué es Simple Llama?

**Simple Llama** es un intermediario inteligente y panel web ligero que se coloca frente a `llama-server.exe`. Te permite gestionar múltiples modelos GGUF, hacer **hot-swap** instantáneo, liberar la VRAM cuando estás inactivo y conectar editores como **OpenCode, Zed, VS Code o Open WebUI** con compatibilidad OpenAI.

### 💡 Pilares Fundamentales:

1. **Cero Dependencias Externas:** Escrito 100% con la librería estándar de Python (`http.server`, `urllib`, `threading`, `socketserver`). No necesitas `pip install` ni entornos virtuales pesados.
2. **Servidor Multihilo Concurrente:** No se congela durante la generación de texto. Puedes refrescar el panel o liberar la GPU en cualquier momento.
3. **Liberación de Memoria (Watchdog):** Si no realizas peticiones durante 10 minutos, apaga el modelo y libera el 100% de la VRAM a 0 MB.
4. **Poda de RAM Física (`EmptyWorkingSet`):** Mantiene el consumo de memoria del sistema en **< 40 MB** en lugar de los 2.5–3 GB que consume Ollama.
5. **Acceso Seguro a Red Local (Wi-Fi):** Activa o desactiva con un solo clic el acceso desde tablets o celulares en tu red local con persistencia de configuración.

---

## 🚀 Inicio Rápido (Quickstart)

### 1. Requisitos Previos
- **Windows 10/11** o Linux con tarjeta gráfica NVIDIA (CUDA).
- **Python 3.10 o superior** (probado y compatible hasta Python 3.13+).
- Binario oficial de **[llama.cpp](https://github.com/ggerganov/llama.cpp/releases)** (`llama-server.exe` con CUDA).

### 2. Instalación y Estructura Recomendada

Clona el repositorio dentro de tu directorio de trabajo de `llama.cpp`:

```bash
git clone https://github.com/Nelsoncg98/simple-llama.git
```

Estructura de carpetas recomendada:

```text
llamacpp/                          # Carpeta base donde colocas llama.cpp
├── simple-llama/                  # Repositorio clonado
│   ├── proxy.py                   # Servidor HTTP multihilo y proxy API
│   ├── manager.py                 # Gestor de procesos GPU, watchdog y hot-swap
│   ├── config.py                  # Descubrimiento modular de modelos
│   ├── sync_opencode.py           # Sincronizador de clientes (OpenCode / Pi Agent)
│   ├── templates/
│   │   └── dashboard.html         # Panel de control web
│   ├── iniciar-servidor.bat       # Lanzador en un clic
│   └── configopencode.bat         # Script de sincronización de modelos
├── models/                        # Directorio con tus modelos locales
│   ├── gemma/                     # Carpeta de familia o modelo
│   │   ├── gemma-2-9b-it-Q4_K_M.gguf
│   │   └── config.json            # Configuración del modelo
│   └── qwen/
│       ├── Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
│       └── config.json
├── llama-server.exe               # Binario oficial de llama.cpp (release con CUDA)
└── *.dll                          # DLLs de CUDA / OpenMP requeridas por llama-server
```

> 💡 **Detección inteligente:** `config.py` detecta automáticamente si `llama-server.exe` y `models/` residen en la carpeta padre (`..`) o dentro de la misma carpeta del orquestador.

### 3. Ejecución

Haz doble clic en **`iniciar-servidor.bat`** o ejecuta en tu terminal:

```bash
python proxy.py
```

Al iniciar:
- Se abrirá automáticamente tu navegador en **`http://localhost:11435`**.
- La API compatible con OpenAI estará lista en **`http://localhost:11435/v1`**.
- El backend nativo de `llama.cpp` se levantará bajo demanda en **`http://localhost:11436`**.

---

## ⚙️ Configuración de Modelos (`config.json`)

Cada subcarpeta dentro de `models/` debe contener su propio `config.json`. Existen dos formatos admitidos:

### 1. Formato Simple (Un solo perfil)

Plantilla con todos los parámetros soportados lista para copiar y pegar en `models/<tu-carpeta>/config.json`. Los valores mostrados son los recomendados/por defecto (cualquier parámetro que omitas tomará automáticamente su valor de la tabla):

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

### 2. Formato Multi-Perfil (Múltiples contextos o variantes)

Útil para definir diferentes tamaños de contexto (`64k`, `72k`, `81k`) o modalidades (`code` vs `chat`) para un mismo modelo sin duplicar archivos:

```json
{
  "family": "Ornith 1.5 (9B)",
  "reasoning_budget": 8192,
  "profiles": {
    "ornith-code": {
      "model_path": "Ornith-1.5-9B.gguf",
      "ctx": 65536,
      "desc": "Ornith 1.5 9B - Modo Código (64k tokens, anti-bucle 1.08)",
      "badge": "64k Code",
      "badge_color": "#0284c7",
      "temp": 0.60,
      "top_p": 0.95,
      "top_k": 20,
      "presence_penalty": 0.0,
      "repeat_penalty": 1.08,
      "system_prompt": "..."
    },
    "ornith-chat": {
      "model_path": "Ornith-1.5-9B.gguf",
      "ctx": 98304,
      "desc": "Ornith 1.5 9B - Modo General (96k tokens híbrido)",
      "badge": "96k Chat",
      "badge_color": "#10b981",
      "cache_type_k": "q8_0",
      "cache_type_v": "q4_0",
      "temp": 1.0,
      "presence_penalty": 1.5,
      "repeat_penalty": 1.0,
      "system_prompt": "..."
    }
  }
}
```

---

### 📋 Referencia de Parámetros

| Parámetro | Tipo | Por Defecto (DEFAULT) | Descripción |
| :--- | :---: | :---: | :--- |
| **`family`** | string | *Obligatorio* | Nombre de la familia de modelos mostrado en el dashboard. |
| **`model_path`** | string | *Obligatorio* | Nombre del archivo `.gguf` dentro de la carpeta. |
| **`ctx`** | integer | **`8192`** | Tamaño máximo de la ventana de contexto en tokens. |
| **`desc`** | string | `"<family> (<ctx>)"` | Descripción mostrada en la tarjeta del panel web. |
| **`badge`** | string | `"<ctx/1024>k Tokens"` | Texto de la etiqueta en la tarjeta. |
| **`badge_color`** | string | `"#0284c7"` | Color hexadecimal del badge (azul por defecto). |
| **`vision`** | boolean | **`false`** | Activa capacidades multimodales si existe proyector. |
| **`projector_path`** | string | `null` | Archivo `mmproj-*.gguf` de visión (opcional). |
| **`cache_type_k`** | string | **`"q8_0"`** | Tipo de cuantización para las llaves del KV Cache (`q8_0`, `q4_0`, `f16`). |
| **`cache_type_v`** | string | **`"q8_0"`** | Tipo de cuantización para los valores del KV Cache (`q8_0`, `q4_0`, `f16`). |
| **`fa`** | string | **`"on"`** | Flash Attention. `"on"` activado para máximo ahorro de VRAM. |
| **`temp`** | float | **`0.70`** | Temperatura de muestreo (creatividad/entropía). |
| **`top_p`** | float | **`0.95`** | Muestreo por probabilidad acumulada (Nucleus sampling). |
| **`top_k`** | integer | **`40`** | Limita el muestreo a los mejores K tokens candidatos. |
| **`min_p`** | float | `null` *(0.0)* | Probabilidad mínima relativa al token más probable. |
| **`repeat_penalty`** | float | `null` *(1.0)* | Penalización por repetición (1.08 recomendado para evitar bucles). |
| **`presence_penalty`** | float | `null` *(0.0)* | Penalización de presencia (1.50 recomendado en perfiles conversacionales). |
| **`reasoning_budget`** | integer | `null` *(-1)* | Límite máximo de tokens de razonamiento (`<think>`). |
| **`system_prompt`** | string | `null` | Prompt de sistema inyectado automáticamente en cada petición. |
| **`chat_template`** | string | `null` | Plantilla Jinja alternativa (omite para usar la del GGUF). |
| **`draft_model`** | string | `null` | Archivo `.gguf` para aceleración por decoding especulativo. |
| **`spec_type`** | string | `null` | Tipo de decoding especulativo (ej: `"draft-mtp"`). |

---

## 🌐 Control de Red Local (Wi-Fi / LAN)

Simple Llama incluye un firewall a nivel de aplicación conmutador de privacidad integrado en el panel web:

- **Modo OFF (Por defecto):** Solo la propia PC (`localhost` / `127.0.0.1`) puede acceder al dashboard y a la API. Cualquier dispositivo externo en la red Wi-Fi recibe un error `403 Forbidden`.
- **Modo ON:** Permite conexiones de otros dispositivos en tu red local (móvil, tablet, laptop secundaria). El panel muestra tu IP local exacta (ej. `http://192.168.18.78:11435`).
- **Persistente:** El estado se guarda automáticamente en `settings.json`. Si apagas o reinicias la máquina, se mantendrá tu preferencia anterior.
- **Seguridad:** El botón solo puede ser accionado desde la propia máquina local para prevenir cambios no autorizados.

---

## 📊 Comparativa: Simple Llama vs. Ollama

| Característica | Ollama Estándar | Simple Llama |
| :--- | :---: | :---: |
| **Consumo de RAM Física** | 2.3 – 3.2 GB | **< 40 MB** (Poda `EmptyWorkingSet`) |
| **Reposo de GPU (Inactividad)** | Ocupa VRAM continuamente | **Watchdog:** 0 MB VRAM tras 10 min |
| **Cambio de Modelo (Hot-swap)** | Puede dejar procesos huérfanos | **Atómico y limpio:** Cierra previo antes del nuevo |
| **Flash Attention (FA)** | Variable según versión | **Forzado en GPU (`-fa on`)** |
| **Control de KV Cache** | Automático no configurable | **Personalizable (`q8_0`, `q4_0`)** |
| **Multihilo en Streaming** | Varía | **Sí:** Panel y control accesibles durante inferencia |
| **Dependencias del Sistema** | Servicio en segundo plano / Go | **Cero dependencias:** Python estándar puro |

---

## 🔌 Conexión con Clientes y Editores

Simple Llama expone una API compatible con el estándar de OpenAI en el puerto **11435**.

### 1. OpenCode / Pi Agent
Ejecuta el script incluido **`configopencode.bat`** (o `python sync_opencode.py`). Escaneará tus modelos registrados y los inyectará automáticamente en:
- `~/.config/opencode/opencode.jsonc`
- `~/.pi/agent/models.json`

### 2. Zed Editor
Configura tu proveedor en `settings.json` de Zed:

```json
{
  "language_models": {
    "openai": {
      "api_url": "http://localhost:11435/v1",
      "available_models": ["ornith-edge", "qwen-coder"]
    }
  }
}
```

### 3. Open WebUI / Cursor / VS Code
- **Base URL:** `http://localhost:11435/v1`
- **API Key:** `sk-local` (o cualquier texto, no es requerido).

---

## ⚠️ Recomendaciones para GPUs de 4–6 GB VRAM

1. **Evita nombres reservados en perfiles:** No uses `server`, `load`, `unload`, `health`, ni `props`. Usa nombres descriptivos como `ornith-low`, `qwen-code`.
2. **Estimación de VRAM:**
   - Modelos **3B – 4B**: ~3.2 GB VRAM.
   - Modelos **7B – 9B**: ~4.2 – 4.8 GB base.
   - En una RTX 3050 (6 GB), para modelos 9B con KV Cache Q8 puro (`q8_0/q8_0`), el límite seguro de contexto es **81k tokens** (~5.7 GB). Si requieres contextos mayores (hasta 96k o 128k), usa caché híbrido (`cache_type_v: "q4_0"`).
3. **Anti-Bucle en Código:** Si tu modelo entra en bucles repetitivos en modo programación, configura `"repeat_penalty": 1.08`.

---

## 📄 Licencia

Distribuido bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más información.
