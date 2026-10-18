"""
Gestor de Procesos, Hot-Swap, Watchdog y Optimización de Memoria (manager.py)
Simple Llama
"""

import time
import subprocess
import threading
import urllib.request
import urllib.error
import os
import sys

from config import (
    BASE_DIR,
    LLAMACPP_DIR,
    LLAMA_SERVER_BIN,
    BACKEND_HOST,
    BACKEND_PORT,
    get_idle_timeout_seconds,
    MODELS,
    DEFAULT_MODEL,
    reload_models,
)


class ModelManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.current_model = None
        self.current_params = None
        self.process = None
        self.last_activity = time.time()
        self.running = True
        self.is_busy = False
        self._last_slots_state = None
        self.last_error = None

        # Iniciar hilo de vigilancia (Watchdog para auto-apagado)
        self.watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self.watchdog_thread.start()

    def get_last_error(self):
        """Retorna el último error reportado y lo limpia."""
        err = self.last_error
        self.last_error = None
        return err

    def start_inference(self):
        """Bloquea el watchdog mientras se genera texto"""
        with self.lock:
            self.is_busy = True
            self.last_activity = time.time()

    def end_inference(self):
        """Desbloquea el watchdog y empieza a contar la inactividad desde este momento"""
        with self.lock:
            self.is_busy = False
            self.last_activity = time.time()

    def touch(self):
        """Registra actividad reciente para resetear el temporizador de auto-apagado"""
        self.last_activity = time.time()

    def _trim_memory(self, pid):
        """
        Limpia la caché de lectura de Windows en el proceso para mantener
        la memoria RAM física en ~40 MB en lugar de 2.5 GB.
        """
        try:
            import ctypes
            PROCESS_SET_QUOTA = 0x0100
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                ctypes.windll.psapi.EmptyWorkingSet(handle)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def stop_server(self):
        """Detiene el modelo activo y libera el 100% de la VRAM y RAM de forma segura y concurrente"""
        with self.lock:
            proc = self.process
            model_name = self.current_model
            self.process = None
            self.current_model = None
            self.current_params = None
            self.is_busy = False

            if proc:
                print(f"\n[Manager] [-] Deteniendo modelo actual ('{model_name}')...")
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                print("[Manager] [OK] Servidor detenido. GPU y RAM liberadas (0 MB VRAM).")

    def ensure_model(self, model_name, force_reload=False):
        """
        Conmuta o levanta el modelo solicitado en GPU de forma segura.
        Si ya está corriendo con la misma configuración y sin force_reload, retorna True de inmediato.
        Si se cambiaron parámetros (como ctx o temp) o se fuerza recarga, reinicia el servidor.
        """
        with self.lock:
            self.touch()

            # Normalizar modelo solicitado
            target = model_name
            if target not in MODELS:
                reload_models()
            if target not in MODELS:
                target = DEFAULT_MODEL

            new_config = MODELS.get(target, {})
            config_changed = False
            if self.current_params and self.current_model == target:
                keys_to_compare = [
                    "model_path", "ctx", "cache_type_k", "cache_type_v",
                    "vision", "projector_path", "draft_path", "spec_type",
                    "fa", "temp", "top_p", "top_k", "repeat_penalty", "presence_penalty"
                ]
                for k in keys_to_compare:
                    if self.current_params.get(k) != new_config.get(k):
                        config_changed = True
                        break

            # Si ya está corriendo el modelo correcto, no se forzó recarga y la config no cambió, continuar
            if self.current_model == target and not force_reload and not config_changed and self.process and self.process.poll() is None:
                return True

            if config_changed and self.current_model == target:
                print(f"\n[Manager] [i] Se detectaron cambios en la configuración de '{target}'. Reiniciando servidor...")

            # Si hay otro modelo corriendo (o se forzó recarga / cambió la config), detenerlo primero
            self.stop_server()

            config = MODELS[target]
            model_path = config["model_path"]
            ctx = config["ctx"]
            use_vision = config.get("vision", False)
            projector_path = config.get("projector_path", None)

            # Validar existencia de archivos
            if not os.path.exists(model_path):
                self.last_error = f"No se encontró el archivo del modelo: {os.path.basename(model_path)}"
                print(f"[Manager] [!] ERROR: {self.last_error}\n    {model_path}")
                return False

            if use_vision and (not projector_path or not os.path.exists(projector_path)):
                self.last_error = f"No se encontró el proyector de visión: {os.path.basename(projector_path) if projector_path else 'Sin especificar'}"
                print(f"[Manager] [!] ERROR: {self.last_error}")
                return False

            cache_type_k = config.get("cache_type_k", "q8_0")
            cache_type_v = config.get("cache_type_v", "q8_0")

            draft_path = config.get("draft_path", None)
            spec_type = config.get("spec_type", "draft-mtp" if draft_path else None)
            spec_draft_n_max = config.get("spec_draft_n_max", None)
            fa_setting = config.get("fa")
            if fa_setting is None:
                fa_setting = "off" if draft_path else "on"
            fa_arg = "off" if (fa_setting is False or str(fa_setting).lower() == "off") else "on"

            vision_label = "SI" if use_vision else "NO"
            if draft_path:
                draft_label = f"SI ({os.path.basename(draft_path)})"
            elif spec_type:
                draft_label = f"SI (Nativo {spec_type})"
            else:
                draft_label = "NO"
            print(f"\n[Manager] [*] Levantando '{target}' (Contexto: {ctx} tokens, Cache: K={cache_type_k}/V={cache_type_v}, Vision: {vision_label}, Draft/MTP: {draft_label}, FA: {fa_arg}) en GPU...")

            cmd = [
                LLAMA_SERVER_BIN,
                "-m", model_path,
                "-ngl", "99",
                "-c", str(ctx),
                "-fa", fa_arg,
                "--cache-type-k", str(cache_type_k),
                "--cache-type-v", str(cache_type_v),
                "--alias", target,
                "--port", str(BACKEND_PORT),
                "--host", "0.0.0.0",
                "--log-verbosity", "2",
            ]

            if draft_path and os.path.exists(draft_path):
                cmd.extend(["-md", draft_path, "-ngld", "99"])

            if spec_type:
                cmd.extend(["--spec-type", spec_type])
                if spec_draft_n_max:
                    cmd.extend(["--spec-draft-n-max", str(spec_draft_n_max)])

            if use_vision and projector_path:
                cmd.extend(["--mmproj", projector_path])

            temp = config.get("temp", None)
            top_p = config.get("top_p", None)
            top_k = config.get("top_k", None)
            repeat_penalty = config.get("repeat_penalty", None)
            presence_penalty = config.get("presence_penalty", None)
            min_p = config.get("min_p", None)
            reasoning_effort = config.get("reasoning_effort", None)

            if temp is not None:
                cmd.extend(["--temp", str(temp)])
            if top_p is not None:
                cmd.extend(["--top-p", str(top_p)])
            if top_k is not None:
                cmd.extend(["--top-k", str(top_k)])
            if min_p is not None:
                cmd.extend(["--min-p", str(min_p)])
            if repeat_penalty is not None:
                cmd.extend(["--repeat-penalty", str(repeat_penalty)])
            if presence_penalty is not None:
                cmd.extend(["--presence-penalty", str(presence_penalty)])
            if reasoning_effort is not None:
                cmd.extend(["--reasoning-effort", str(reasoning_effort)])

            reasoning_budget = config.get("reasoning_budget", None)
            if reasoning_budget is not None:
                cmd.extend(["--reasoning-budget", str(reasoning_budget)])

            chat_template = config.get("chat_template", None)
            if chat_template:
                cmd.extend(["--chat-template", str(chat_template)])

            log_path = os.path.join(LLAMACPP_DIR, "server.log")
            self._server_log_file = open(log_path, "w", encoding="utf-8", errors="replace")

            self.process = subprocess.Popen(
                cmd,
                cwd=LLAMACPP_DIR,
                stdout=self._server_log_file,
                stderr=subprocess.STDOUT,
            )
            self.current_model = target
            self.current_params = dict(config)

            # Esperar a que el servidor esté listo respondiendo en /health
            start_wait = time.time()
            ready = False
            while time.time() - start_wait < 60:
                # Si el proceso murió prematuramente, reportar el error exacto de server.log
                if self.process.poll() is not None:
                    break

                try:
                    req = urllib.request.Request(f"http://{BACKEND_HOST}:{BACKEND_PORT}/health")
                    with urllib.request.urlopen(req, timeout=1) as res:
                        if res.status == 200:
                            ready = True
                            break
                except Exception:
                    time.sleep(0.5)

            if ready:
                elapsed = round(time.time() - start_wait, 1)
                print(f"[Manager] [+] '{target}' listo en {elapsed}s. Aceleracion GPU activa.")
                if self.process:
                    self._trim_memory(self.process.pid)
                return True
            else:
                # Leer las últimas líneas de server.log para mostrar el motivo exacto
                try:
                    self._server_log_file.flush()
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = [l.strip() for l in f.readlines() if l.strip()]
                        err_tail = "\n".join(lines[-10:]) if lines else "Sin salida en log."
                except Exception:
                    err_tail = "No se pudo leer server.log."

                print(f"[Manager] [!] Error al iniciar '{target}'. Motivo exacto de llama-server:\n{err_tail}\n")
                self.last_error = f"Error al iniciar '{target}':\n{err_tail}"
                self.stop_server()
                return False

    def trim_current_model(self):
        """Fuerza la liberación de memoria RAM física del proceso activo"""
        with self.lock:
            if self.process and self.process.poll() is None:
                self._trim_memory(self.process.pid)

    def _watchdog_loop(self):
        """Vigila la inactividad y apaga el proceso tras 10 minutos sin uso"""
        while self.running:
            time.sleep(15)
            try:
                with self.lock:
                    if self.process and self.process.poll() is None:
                        # Mantener la RAM física podada continuamente en ~40 MB
                        self._trim_memory(self.process.pid)

                        is_backend_busy = False
                        # Detectar actividad directa en el Chat Web de llama.cpp (8081 / llama-ui)
                        try:
                            slots_req = urllib.request.Request(f"http://{BACKEND_HOST}:{BACKEND_PORT}/slots")
                            with urllib.request.urlopen(slots_req, timeout=1) as slots_res:
                                if slots_res.status == 200:
                                    slots_json = json.loads(slots_res.read().decode("utf-8"))
                                    current_state = []
                                    for slot in slots_json:
                                        if slot.get("is_processing", False):
                                            is_backend_busy = True
                                        current_state.append((slot.get("id_task", -1), slot.get("n_past", -1)))

                                    # Si está procesando o hubo un prompt nuevo en llama-ui, renovar actividad
                                    if is_backend_busy or (self._last_slots_state is not None and current_state != self._last_slots_state):
                                        self.last_activity = time.time()
                                    self._last_slots_state = current_state
                        except Exception:
                            pass

                        # Si el proxy está generando o el backend está ocupado, renovar y JAMÁS apagar
                        if self.is_busy or is_backend_busy:
                            self.last_activity = time.time()
                            continue

                        timeout_sec = get_idle_timeout_seconds()
                        if timeout_sec > 0:
                            idle_time = time.time() - self.last_activity
                            if idle_time > timeout_sec:
                                mins = int(idle_time // 60)
                                print(f"\n[Watchdog] [-] {mins} min de inactividad detectados. Auto-apagando para liberar GPU...")
                                self.stop_server()
            except Exception:
                pass


# Instancia única del gestor
manager = ModelManager()
