# ⚡ Simple Llama

> **Orquestador local ultra-ligero para [llama.cpp](https://github.com/ggerganov/llama.cpp)**. Diseñado con cero dependencias externas para ejecutar, conmutar y gestionar modelos de Inteligencia Artificial con aceleración **NVIDIA CUDA** y consumo ultra-eficiente de memoria.

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

**Simple Llama** es un intermediario inteligente, servidor proxy multihilo y panel de control web que se sitúa frente al motor `llama-server.exe`. Permite gestionar modelos GGUF, realizar **hot-swap** instantáneo, ajustar el contexto y parámetros de muestreo en caliente, liberar la VRAM en periodos de inactividad y conectar clientes como **Zed Editor, OpenCode, Cursor, VS Code o Open WebUI** mediante una API compatible con el estándar de OpenAI.

### 💡 Características Principales:

1. **Cero Dependencias Externas:** 100% Python estándar (`http.server`, `urllib`, `threading`, `subprocess`, `json`). Sin `pip install`, sin entornos virtuales pesados.
2. **Servidor Concurrente Multihilo:** No se bloquea durante la inferencia o streaming. El panel de control responde instantáneamente en cualquier momento.
3. **Watchdog de Inactividad Inteligente:** Temporizador ajustable en caliente desde el panel (**10 min, 30 min, 60 min o continuo**) que apaga el modelo y libera el 100% de la VRAM (0 MB) cuando dejas de usarlo.
4. **Poda de RAM Física (`EmptyWorkingSet`):** Mantiene el consumo del orquestador en **< 40 MB de RAM**, a diferencia de servicios pesados como Ollama (2.5 – 3.2 GB).
5. **Configuración Centralizada Única:** Todos los perfiles se almacenan en un único archivo `models/config.json`. Sin escaneos invasivos fuera de su directorio.
6. **Panel Web Moderno con CRUD Total:**
   - **Crear perfiles:** Asistente interactivo con detección automática de archivos `.gguf`, adaptadores de visión (`mmproj`) y drafts especulativos (`MTP`).
   - **Editar perfiles en caliente:** Renombrado seguro de ID con sanitización automática, selector de presets de contexto (`4k` a `256k`), afinación de `min_p`, `reasoning_budget` (tokens de pensamiento), KV Cache K/V y Flash Attention.
   - **Eliminación segura:** Modal de confirmación con doble verificación que retira el perfil sin tocar tus archivos de modelo en disco.
   - **Hot-Reload:** Si editas los parámetros de un modelo que ya está corriendo en GPU, el orquestador lo reinicia automáticamente con la nueva configuración.
7. **Control de Red Local (Wi-Fi / LAN) y Multi-dispositivo:** Activa o desactiva con un solo clic el acceso desde tablets, móviles u otras PCs. Ajusta dinámicamente los endpoints a la IP real del host y ofrece compatibilidad con portapapeles móvil en entornos HTTP.

---

## 🚀 Inicio Rápido (Quickstart)

### 1. Requisitos Previos
- **Windows 10/11** o **Linux** con GPU NVIDIA y soporte CUDA.
- **Python 3.10 o superior** (compatible hasta Python 3.13+).
- Binario oficial de **[llama.cpp](https://github.com/ggerganov/llama.cpp/releases)** (`llama-server.exe` compilado con CUDA).

### 2. Estructura de Carpetas

```text
llamacpp/
├── llama-server.exe               # Binario oficial de llama.cpp (CUDA)
├── *.dll                          # Bibliotecas CUDA / OpenMP requeridas
└── orchestrator/                  # Repositorio Simple Llama
    ├── proxy.py                   # Servidor HTTP multihilo y proxy OpenAI
    ├── manager.py                 # Gestor de procesos GPU, watchdog y hot-swap
    ├── config.py                  # Motor central de configuración y catálogo
    ├── sync_opencode.py           # Sincronizador para OpenCode y agentes
    ├── templates/
    │   └── dashboard.html         # Interfaz web de administración
    ├── models/                    # Directorio de modelos locales
    │   ├── config.json            # Configuración única de todos los perfiles
    │   ├── modelo.gguf            # Pesos del modelo principal
    │   ├── mmproj-F16.gguf        # Proyector de visión multimodal (opcional)
    │   └── mtp-draft.gguf         # Acelerador speculative / MTP (opcional)
    └── iniciar-servidor.bat       # Lanzador en un clic
```

### 3. Ejecución

Ejecuta el archivo **`iniciar-servidor.bat`** o arranca desde terminal:

```bash
python proxy.py
```

Al iniciar:
* **Panel Web de Control:** [`http://localhost:11435`](http://localhost:11435)
* **Endpoint API OpenAI:** `http://localhost:11435/v1`
* **Chat Web Nativo de llama.cpp:** `http://localhost:11436` (se levanta bajo demanda al cargar cualquier modelo)

---

## ⚙️ Configuración Centralizada (`models/config.json`)

El catálogo de modelos se gestiona en un archivo JSON estructurado por identificador de perfil:

```json
{
  "models": {
    "gemma12b-q3ks-edge": {
      "family": "Gemma 4 12B",
      "model_path": "gemma-4-12b-it-Q3_K_S.gguf",
      "ctx": 16384,
      "desc": "Gemma 4 12B Q3_K_S - Punto dulce 16k tokens, 100% GPU",
      "badge": "Q3_K_S 16k",
      "badge_color": "#10b981",
      "vision": false,
      "fa": "on",
      "cache_type_k": "q4_0",
      "cache_type_v": "q4_0",
      "temp": 1.0,
      "top_p": 0.95,
      "top_k": 64,
      "min_p": 0.05,
      "repeat_penalty": 1.08,
      "presence_penalty": 0.50
    },
    "ornith-edge": {
      "family": "Ornith 1.5 (9B)",
      "model_path": "Ornith-1.5-9B-GSQ-RCO-IQ3_M.gguf",
      "ctx": 73728,
      "desc": "Ornith 1.5 9B - 72k tokens con razonamiento nativo",
      "badge": "72k Recomendado",
      "badge_color": "#10b981",
      "vision": false,
      "fa": "on",
      "cache_type_k": "q8_0",
      "cache_type_v": "q8_0",
      "temp": 0.60,
      "top_p": 0.95,
      "top_k": 20,
      "repeat_penalty": 1.08,
      "reasoning_budget": 8192,
      "system_prompt": "Eres un asistente de inteligencia artificial útil y preciso..."
    }
  }
}
```

---

### 📋 Referencia de Parámetros de Perfil

| Parámetro | Tipo | Por Defecto | Descripción |
| :--- | :---: | :---: | :--- |
| **`family`** | `string` | `"General"` | Familia o agrupación visual en el dashboard. |
| **`model_path`** | `string` | *Obligatorio* | Nombre del archivo `.gguf` en `models/`. |
| **`ctx`** | `integer` | `8192` | Límite máximo de contexto en tokens (admite presets de 4k a 256k). |
| **`desc`** | `string` | Auto | Descripción mostrada en la tarjeta del modelo. |
| **`badge`** | `string` | Auto | Texto de la etiqueta distintiva. |
| **`badge_color`** | `string` | `"#38bdf8"` | Color hexadecimal del badge (admite selector visual y swatches). |
| **`vision`** | `boolean` | `false` | Indica si el perfil soporta imágenes multimodales. |
| **`projector_path`** | `string` | `null` | Nombre del archivo `mmproj-*.gguf` para visión. |
| **`draft_path`** | `string` | `null` | Archivo de modelo borrador para speculative decoding (o `__embedded__`). |
| **`spec_type`** | `string` | `null` | Tipo de especulación (ej. `"draft-mtp"`). |
| **`cache_type_k`** | `string` | `"q8_0"` | Cuantización de llaves de atención (`q8_0` recomendado, `q4_0`, `f16`). |
| **`cache_type_v`** | `string` | `"q8_0"` | Cuantización de valores de atención (`q8_0` recomendado, `q4_0`, `f16`). |
| **`fa`** | `string` | `"on"` | Flash Attention (`"on"` / `"off"`). Reduce uso de VRAM a $O(N)$. |
| **`temp`** | `float` | `0.70` | Temperatura de muestreo estocástico. |
| **`top_p`** | `float` | `0.95` | Muestreo por probabilidad acumulada (Nucleus). |
| **`top_k`** | `integer` | `40` | Límite de tokens candidatos considerados. |
| **`min_p`** | `float` | `null` | Muestreo Min-P (probabilidad mínima relativa al mejor token). |
| **`repeat_penalty`** | `float` | `null` | Penalización por repetición (ej. `1.08` para evitar bucles). |
| **`presence_penalty`** | `float` | `null` | Penalización de presencia por aparición de nuevos temas. |
| **`reasoning_budget`** | `integer` | `null` | Presupuesto máximo de tokens de pensamiento (`<think>`). |
| **`system_prompt`** | `string` | `null` | Directiva maestra inyectada automáticamente en las conversaciones. |

---

## 🌐 Control de Red Local (Wi-Fi / LAN)

Simple Llama incluye un cortafuegos a nivel de aplicación conmutador de privacidad integrado en el panel web:

* **Modo OFF (Por defecto):** Solo la propia PC (`localhost` / `127.0.0.1`) puede acceder al dashboard y a la API. Peticiones de IPs externas reciben `403 Forbidden`.
* **Modo ON:** Autoriza conexiones de dispositivos en tu red local (tablets, teléfonos, laptops secundarias). El panel ajusta dinámicamente los endpoints a tu IP LAN real (ej. `http://192.168.18.78:11435`).
* **Persistencia:** La preferencia se almacena en `settings.json` y se conserva tras reiniciar el equipo o el servidor.
* **Control total desde LAN:** Al habilitar la red local, puedes administrar, crear, editar y eliminar perfiles directamente desde tu tablet o dispositivo móvil.

---

## 📊 Comparativa: Simple Llama vs. Ollama

| Característica | Ollama Estándar | Simple Llama |
| :--- | :---: | :---: |
| **Consumo de RAM Física** | 2.5 – 3.2 GB | **< 40 MB** (Poda `EmptyWorkingSet`) |
| **Reposo de GPU (Inactividad)** | Mantiene VRAM ocupada | **Watchdog:** 0 MB VRAM tras timeout configurable |
| **Cambio de Modelo (Hot-swap)** | Puede dejar procesos huérfanos | **Atómico y limpio:** Cierra previo antes del nuevo |
| **Flash Attention (FA)** | Variable según versión | **Control explícito (`-fa on`)** |
| **Control de KV Cache** | Fijo no configurable | **Personalizable por perfil (`q8_0`, `q4_0`, `f16`)** |
| **Concurrencia en Streaming** | Panel bloqueado a menudo | **Multihilo:** Panel y control accesibles durante inferencia |
| **Dependencias del Sistema** | Servicio en segundo plano / Go | **Cero dependencias externas:** Python estándar puro |

---

## 🔌 Conexión con Clientes y Editores

Simple Llama expone una API compatible con el estándar de OpenAI en el puerto **11435**.

### 1. Zed Editor
Configura tu proveedor en `settings.json` de Zed:

```json
{
  "language_models": {
    "openai": {
      "api_url": "http://localhost:11435/v1",
      "available_models": ["gemma12b-q3ks-edge", "ornith-edge", "qwen-9b-iq3-coding"]
    }
  }
}
```

### 2. OpenCode / Pi Agent
Ejecuta el script incluido **`sync_opencode.py`**: escaneará los modelos activos y los sincronizará automáticamente con tus archivos de configuración locales.

### 3. Cursor / VS Code / Open WebUI
* **Base URL:** `http://localhost:11435/v1`
* **API Key:** `sk-local` (o cualquier valor de texto).
* **Model ID:** El nombre exacto del perfil (ej. `gemma12b-q3ks-edge`, `ornith-edge`).

---

## 📄 Licencia

Distribuido bajo la Licencia **MIT**. Consulta el archivo `LICENSE` para más información.
