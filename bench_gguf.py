#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bench_gguf.py — banc d'essai de modèles .gguf avec llama.cpp.

Pour chaque modèle trouvé :
  1. lit l'en-tête GGUF (archi, params, quant, couche/KV) sans dépendance externe ;
  2. vérifie que le modèle tient en RAM (garde-fou, réduction auto du contexte) ;
  3. lance llama-server, mesure pp / tg / TTFT (3 répétitions, médiane) ;
  4. évalue 40 QCM (scoring par logprobs) + 6 tâches génératives (auto-scoring) ;
  5. écrit results.jsonl / results.csv / summary.md, arrête le serveur, passe au suivant.

Exemple :
  python3 bench_gguf.py --models /root/models --out /root/bench/results \
      --ctx 4096 --threads 6 --reps 3 --eval-set /root/bench/eval_set.json

ATTENTION : le test de code généré exécute du Python produit par le modèle
(dans un sous-processus, sans bac à sable). À réserver à une machine de bench.
"""

import argparse
import csv
import html
import json
import math
import os
import re
import signal
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

# ----------------------------------------------------------------------------
# Journal + état partagé (console, fichier, tableau de bord web)
# ----------------------------------------------------------------------------

STATE = {}
_LOG_PATH = None
_LOG_LOCK = threading.Lock()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _LOG_PATH:
        with _LOG_LOCK:
            try:
                with open(_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass


def set_state(**kw):
    STATE.update(kw)
    STATE["updated"] = time.time()
    path = STATE.get("_path")
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# GGUF : lecture de l'en-tête (métadonnées + formes des tenseurs)
# ----------------------------------------------------------------------------

_GGUF_SCALARS = {
    0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2),
    4: ("I", 4), 5: ("i", 4), 6: ("f", 4), 7: ("?", 1),
    10: ("Q", 8), 11: ("q", 8), 12: ("d", 8),
}

_FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS",
    24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ2_S", 28: "IQ4_XS",
    29: "I8", 30: "I16", 31: "I32", 32: "I64", 33: "F64", 34: "IQ1_M",
    35: "BF16", 36: "Q4_0_4_4", 37: "Q4_0_4_8", 38: "Q4_0_8_8",
    39: "TQ1_0", 40: "TQ2_0",
}


class GGUFMeta:
    """Lecture tolérante de l'en-tête GGUF (premiers 64 Mo)."""

    def __init__(self, path, max_bytes=64 * 1024 * 1024):
        self.path = str(path)
        self.kv = {}
        self.params = None
        self.n_tensors = None
        self._buf = None
        self._p = 0
        self._ok = False
        try:
            with open(self.path, "rb") as f:
                self._buf = memoryview(f.read(max_bytes))
            self._parse()
            self._ok = True
        except Exception as exc:  # noqa: BLE001 - on veut continuer coûte que coûte
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._buf = None

    # -- primitives de lecture ------------------------------------------------
    def _u32(self):
        v = struct.unpack_from("<I", self._buf, self._p)[0]
        self._p += 4
        return v

    def _u64(self):
        v = struct.unpack_from("<Q", self._buf, self._p)[0]
        self._p += 8
        return v

    def _str(self):
        n = self._u64()
        if self._p + n > len(self._buf):
            raise ValueError("buffer trop court")
        v = bytes(self._buf[self._p:self._p + n]).decode("utf-8", "replace")
        self._p += n
        return v

    def _skip_str(self):
        n = self._u64()
        self._p += n

    def _value(self, t):
        if t in _GGUF_SCALARS:
            fmt, size = _GGUF_SCALARS[t]
            v = struct.unpack_from("<" + fmt, self._buf, self._p)[0]
            self._p += size
            return v
        if t == 8:
            return self._str()
        if t == 9:
            et = self._u32()
            n = self._u64()
            if et == 8:
                for _ in range(n):
                    self._skip_str()
                return f"<{n} strings>"
            if et in _GGUF_SCALARS:
                size = _GGUF_SCALARS[et][1]
                self._p += size * n
                return f"<{n} scalaires>"
            raise ValueError("tableau imbriqué")
        raise ValueError(f"type GGUF inconnu: {t}")

    def _parse(self):
        if bytes(self._buf[:4]) != b"GGUF":
            raise ValueError("magic GGUF absent")
        self._p = 4
        version = self._u32()
        self.n_tensors = self._u64()
        n_kv = self._u64()
        keys_of_interest = {
            "general.architecture", "general.name", "general.file_type",
            "general.parameter_count", "tokenizer.chat_template",
            "tokenizer.ggml.model",
        }
        arch = None
        for _ in range(n_kv):
            key = self._str()
            t = self._u32()
            want = key in keys_of_interest or key.endswith((
                ".block_count", ".context_length", ".embedding_length",
                ".attention.head_count", ".attention.head_count_kv",
                ".expert_count", ".expert_used_count", ".attention.key_length",
                ".vision.patch_size", ".rope.freq_base",
            ))
            if not want:
                if t == 9:
                    self._value(t)
                elif t == 8:
                    self._skip_str()
                else:
                    fmt, size = _GGUF_SCALARS[t]
                    self._p += size
                continue
            val = self._value(t)
            self.kv[key] = val
            if key == "general.architecture":
                arch = val

        # paramètres exacts depuis les formes de tenseurs
        total = 0
        for _ in range(self.n_tensors):
            self._skip_str()
            nd = self._u32()
            dims = [self._u64() for _ in range(nd)]
            self._u32()   # ggml_type
            self._u64()   # offset
            p = 1
            for d in dims:
                p *= d
            total += p
        self.params = total
        self.arch = arch

    # -- accès ---------------------------------------------------------------
    def get(self, suffix, default=None):
        if suffix in self.kv:
            return self.kv[suffix]
        for k, v in self.kv.items():
            if k.endswith("." + suffix):
                return v
        return default

    @property
    def block_count(self):
        return self.get("block_count") or 0

    @property
    def head_count(self):
        return self.get("attention.head_count") or 0

    @property
    def head_count_kv(self):
        return self.get("attention.head_count_kv") or 0

    @property
    def embedding_length(self):
        return self.get("embedding_length") or 0

    @property
    def context_length(self):
        return self.get("context_length") or 0

    @property
    def head_dim(self):
        k = self.get("attention.key_length")
        if k:
            return int(k)
        if self.head_count:
            return int(self.embedding_length / self.head_count)
        return 128

    @property
    def quant(self):
        # le nom de fichier est plus fiable que general.file_type pour les quants
        # "UD-*" (mélange de types) : on le lit en premier.
        m = re.search(r"(UD-Q\d+_K_[A-Z]+|IQ\d+_[A-Z0-9]+|Q\d+_K_[A-Z0-9]+|Q\d+_K|Q\d+_\d|BF16|F16|F32)",
                      Path(self.path).stem, re.I)
        if m:
            return m.group(1).upper()
        ft = self.kv.get("general.file_type")
        if isinstance(ft, int) and ft in _FILE_TYPES:
            return _FILE_TYPES[ft]
        return "?"

    def kv_cache_bytes(self, ctx):
        layers = self.block_count or 32
        kvh = self.head_count_kv or self.head_count or 8
        return 2 * layers * kvh * self.head_dim * ctx * 2  # K+V, f16


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------

def mem_available_gib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 2**30
    except OSError:
        pass
    return 0.0


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def port_pids(port):
    """PIDs des processus qui écoutent sur `port` (lecture de /proc, sans dépendance).

    Équivalent portable de `lsof -ti :PORT`, utile pour arrêter le tableau de bord
    même si lsof/fuser ne sont pas installés.
    """
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            cols = line.split()
            if len(cols) < 10 or cols[3] != "0A":  # 0A = LISTEN
                continue
            try:
                local_port = int(cols[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port:
                inodes.add(cols[9])
    if not inodes:
        return []

    wanted = {f"socket:[{i}]" for i in inodes}
    pids = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    if os.readlink(fd) in wanted:
                        pids.append(int(entry.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return sorted(set(pids))


def _pid_cmdline(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return "?"


def stop_server(port):
    """Arrête le processus qui écoute sur `port` (SIGTERM, puis SIGKILL)."""
    pids = [p for p in port_pids(port) if p != os.getpid()]
    if not pids:
        log(f"Aucun processus n'écoute sur le port {port}.")
        return 1

    for pid in pids:
        log(f"Port {port} : arrêt du PID {pid} — {_pid_cmdline(pid)[:100]}")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            log(f"  impossible d'envoyer SIGTERM : {exc}")

    deadline = time.time() + 5
    while time.time() < deadline and port_pids(port):
        time.sleep(0.2)

    if port_pids(port):
        for pid in port_pids(port):
            if pid == os.getpid():
                continue
            log(f"  PID {pid} résiste, envoi de SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.4)

    rest = [p for p in port_pids(port) if p != os.getpid()]
    if rest:
        log(f"Le port {port} est toujours occupé (PID {', '.join(map(str, rest))}).")
        return 1
    log(f"Port {port} libéré.")
    return 0


def median(values, default=None):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else default


def strip_think(text):
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text, flags=re.S | re.I)
    return text.strip()


def human(n):
    if n is None:
        return "—"
    for unit in ("o", "Ko", "Mo", "Go"):
        if abs(n) < 1000 or unit == "Go":
            return f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} Go"


# ----------------------------------------------------------------------------
# Serveur llama.cpp
# ----------------------------------------------------------------------------

class Server:
    def __init__(self, binary, model, ctx, threads, log_path, extra_flags=None):
        self.binary = binary
        self.model = str(model)
        self.ctx = ctx
        self.threads = threads
        self.log_path = Path(log_path)
        self.extra_flags = extra_flags or []
        self.port = free_port()
        self.proc = None
        self.base = f"http://127.0.0.1:{self.port}"
        self.session = requests.Session()
        self.load_s = None

    def start(self, timeout=420):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.binary,
            "-m", self.model,
            "-c", str(self.ctx),
            "-t", str(self.threads),
            "-b", "2048", "-ub", "512",
            "--host", "127.0.0.1", "--port", str(self.port),
            "-np", "1", "--seed", "42", "--no-warmup",
        ] + self.extra_flags
        t0 = time.perf_counter()
        self.log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                tail = self.log_tail()
                raise RuntimeError(f"llama-server est mort au démarrage (code {self.proc.returncode})\n{tail}")
            try:
                r = self.session.get(self.base + "/health", timeout=2)
                if r.status_code == 200 and r.json().get("status") in ("ok", "no slot available"):
                    self.load_s = time.perf_counter() - t0
                    return self.load_s
            except requests.RequestException:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"llama-server n'a pas répondu en {timeout}s\n{self.log_tail()}")

    def log_tail(self, n=2500):
        try:
            self.log.flush()
            data = Path(self.log_path).read_bytes()
            return data[-n:].decode("utf-8", "replace")
        except OSError:
            return ""

    def post(self, path, payload, timeout=600):
        r = self.session.post(self.base + path, json=payload, timeout=(10, timeout))
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} sur {path}: {r.text[:400]}")
        return r.json()

    def props(self):
        try:
            return self.session.get(self.base + "/props", timeout=10).json()
        except Exception:  # noqa: BLE001
            return {}

    def apply_template(self, messages, kwargs=None):
        for body in ([{"messages": messages, "chat_template_kwargs": kwargs}]
                     if kwargs else []) + [{"messages": messages}]:
            try:
                r = self.post("/apply-template", body, timeout=60)
                if r.get("prompt"):
                    return r["prompt"], bool(kwargs and "chat_template_kwargs" in body)
            except Exception:  # noqa: BLE001
                continue
        return None, False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        try:
            self.log.close()
        except Exception:  # noqa: BLE001
            pass
        self.session.close()


def server_help(binary):
    try:
        out = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=30)
        return (out.stdout or "") + (out.stderr or "")
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------------
# Mesures de vitesse
# ----------------------------------------------------------------------------

PP_TEXT = (
    "La photographie est née au dix-neuvième siècle de l'observation attentive "
    "des phénomènes lumineux et de la chimie des sels d'argent. Les premiers "
    "expérimentateurs cherchaient à fixer sur une surface stable l'image projetée "
    "par une chambre obscure, afin de conserver le témoignage fugace d'un paysage "
    "ou d'un visage. Les procédés se sont succédé, chacun réduisant le temps de pose "
    "et améliorant la finesse des détails obtenus. L'industrie a ensuite standardisé "
    "les formats, les émulsions et les appareils, tandis que les artistes exploraient "
    "les possibilités expressives de ce médium nouveau. "
) * 3

TG_PROMPT = ("Rédige un paragraphe détaillé et continu sur l'histoire de la photographie, "
             "en mentionnant les procédés, les usages sociaux et les évolutions techniques.")


def measure_speed(srv, reps, stream_prompt, timeout, progress=None):
    out = {"pp": [], "pp_n": [], "tg": [], "tg_n": [], "stream": []}
    note = progress or (lambda _msg: None)

    # chauffe
    try:
        srv.post("/completion", {"prompt": "Bonjour", "n_predict": 8, "temperature": 0}, timeout=timeout)
    except Exception:  # noqa: BLE001
        pass

    for i in range(reps):
        note(f"préfill {i + 1}/{reps}")
        r = srv.post("/completion", {
            "prompt": PP_TEXT, "n_predict": 1, "temperature": 0,
            "cache_prompt": False, "ignore_eos": True,
        }, timeout=timeout)
        t = r.get("timings") or {}
        if t.get("prompt_per_second"):
            out["pp"].append(t["prompt_per_second"])
            out["pp_n"].append(t.get("prompt_n"))

    for i in range(reps):
        note(f"génération {i + 1}/{reps}")
        r = srv.post("/completion", {
            "prompt": TG_PROMPT, "n_predict": 128, "temperature": 0,
            "top_k": 1, "cache_prompt": True, "ignore_eos": True,
        }, timeout=timeout)
        t = r.get("timings") or {}
        if t.get("predicted_per_second"):
            out["tg"].append(t["predicted_per_second"])
            out["tg_n"].append(t.get("predicted_n"))

    # mesure "réelle" en streaming : TTFT + débit perçu
    for i in range(max(1, min(2, reps))):
        note(f"streaming {i + 1}/{max(1, min(2, reps))}")
        payload = {
            "prompt": stream_prompt, "n_predict": 128, "temperature": 0,
            "top_k": 1, "cache_prompt": True, "ignore_eos": True, "stream": True,
        }
        t0 = time.perf_counter()
        ttft = None
        timings = {}
        try:
            with srv.session.post(srv.base + "/completion", json=payload,
                                  stream=True, timeout=(10, timeout)) as resp:
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    if raw.startswith(b"data: "):
                        chunk = json.loads(raw[6:])
                        if ttft is None and chunk.get("content"):
                            ttft = time.perf_counter() - t0
                        if chunk.get("stop"):
                            timings = chunk.get("timings") or {}
                            break
            total = time.perf_counter() - t0
            if ttft and total > ttft:
                gen = timings.get("predicted_n") or 128
                out["stream"].append({
                    "ttft_ms": ttft * 1000,
                    "tok_s": gen / (total - ttft),
                })
        except Exception:  # noqa: BLE001
            pass

    return {
        "pp_tok_s": median(out["pp"]),
        "pp_prompt_n": median(out["pp_n"]),
        "tg_tok_s": median(out["tg"]),
        "tg_tokens": median(out["tg_n"]),
        "ttft_ms": median([s["ttft_ms"] for s in out["stream"]]),
        "stream_tok_s": median([s["tok_s"] for s in out["stream"]]),
        "pp_raw": out["pp"], "tg_raw": out["tg"],
    }


# ----------------------------------------------------------------------------
# Évaluation qualité
# ----------------------------------------------------------------------------

LETTERS = "ABCD"


def mcq_messages(item, defaults, order):
    sysmsg = defaults["mcq_system_fr" if item["lang"] == "fr" else "mcq_system_en"]
    body = item["q"] + "\n\n" + "\n".join(
        f"{LETTERS[j]}. {item['choices'][idx]}" for j, idx in enumerate(order))
    body += "\n\nRéponse :" if item["lang"] == "fr" else "\n\nAnswer:"
    return [{"role": "system", "content": sysmsg}, {"role": "user", "content": body}]


def _letter_probs(entry):
    """Extrait la masse de probabilité des lettres A-D (format OpenAI ou legacy)."""
    items = entry.get("top_logprobs") or entry.get("probs") or []
    mass = {}
    for p in items:
        tok = (p.get("token") or p.get("tok_str") or "").strip()
        pr = p.get("prob")
        if pr is None and p.get("logprob") is not None:
            pr = math.exp(p["logprob"])
        if len(tok) == 1 and tok.upper() in LETTERS:
            mass[tok.upper()] = mass.get(tok.upper(), 0.0) + float(pr or 0.0)
    return mass


def ask_letter(srv, prompt, defaults, timeout, stats):
    """Renvoie (probabilités normalisées sur les lettres, texte brut) ou (None, texte)."""
    r = srv.post("/completion", {
        "prompt": prompt, "n_predict": 1, "temperature": 0,
        "n_probs": 40, "cache_prompt": True,
    }, timeout=timeout)
    mass = _letter_probs((r.get("completion_probabilities") or [{}])[0])
    total = sum(mass.values())
    if total > 0.2:
        return {k: v / total for k, v in mass.items()}, r.get("content") or ""
    # le modèle n'a pas répondu par une lettre : on lui laisse quelques tokens
    stats["fallback"] = stats.get("fallback", 0) + 1
    rf = srv.post("/completion", {
        "prompt": prompt, "n_predict": defaults["mcq_fallback_tokens"],
        "temperature": 0, "cache_prompt": True,
    }, timeout=timeout)
    return None, strip_think(rf.get("content") or "")


def run_mcq(srv, items, defaults, timeout, stats, rotations=4, progress=None):
    """QCM avec rotation cyclique des choix.

    Chaque question est posée `rotations` fois avec les propositions décalées,
    ce qui neutralise le biais de position (un modèle qui répond toujours « A »
    n'obtient plus un score artificiel). On moyenne ensuite la probabilité
    accordée à la bonne réponse, ramenée à sa position d'origine.
    """
    results = []
    chat_kwargs = {"enable_thinking": False}
    first_pos_mass = []   # diagnostic : masse moyenne sur la 1re proposition
    total_calls = len(items) * max(1, rotations)
    done_calls = 0
    for item in items:
        n = len(item["choices"])
        want_idx = LETTERS.index(item["answer"])
        acc, used, texts = {}, 0, []
        for rot in range(max(1, rotations)):
            order = [(j + rot) % n for j in range(n)]
            msgs = mcq_messages(item, defaults, order)
            prompt, used_kwargs = srv.apply_template(msgs, chat_kwargs)
            if prompt is None:
                prompt = "\n\n".join(m["content"] for m in msgs) + "\n\nRéponse :"
                stats["no_template"] = stats.get("no_template", 0) + 1
            if used_kwargs:
                stats["thinking_off"] = stats.get("thinking_off", 0) + 1
            try:
                mass, text = ask_letter(srv, prompt, defaults, timeout, stats)
            except Exception as exc:  # noqa: BLE001
                texts.append(f"ERR {str(exc)[:80]}")
                mass, text = None, ""
            done_calls += 1
            if progress:
                progress(done_calls, total_calls)
            if not text and mass is None:
                continue
            texts.append(text[:60].replace("\n", " "))
            if mass:
                used += 1
                first_pos_mass.append(mass.get("A", 0.0))
                for j, letter in enumerate(LETTERS[:n]):
                    acc[order[j]] = acc.get(order[j], 0.0) + mass.get(letter, 0.0)
            else:
                m = re.search(r"\b([ABCD])\b", text.upper())
                if m and LETTERS.index(m.group(1)) < n:
                    used += 1
                    acc[order[LETTERS.index(m.group(1))]] = \
                        acc.get(order[LETTERS.index(m.group(1))], 0.0) + 1.0

        if used:
            probs = {i: acc.get(i, 0.0) / used for i in range(n)}
            pred_idx = max(probs, key=probs.get)
            soft = probs[want_idx]
            correct = pred_idx == want_idx
        else:
            probs, pred_idx, soft, correct = {}, None, None, None

        results.append({
            "id": item["id"], "cat": item.get("cat"), "lang": item.get("lang"),
            "answer": item["answer"],
            "pred": LETTERS[pred_idx] if pred_idx is not None else None,
            "soft": soft, "correct": correct,
            "probs": {LETTERS[i]: round(p, 3) for i, p in probs.items()},
            "rotations": used, "texts": texts,
        })

    soft_vals = [r["soft"] for r in results if r["soft"] is not None]
    judged = [r for r in results if r["correct"] is not None]
    return {
        "items": results,
        "soft_acc": (sum(soft_vals) / len(soft_vals)) if soft_vals else None,
        # non répondu = faux (métrique honnête), la couverture est publiée à part
        "acc": (sum(1 for r in results if r["correct"]) / len(results)) if results else None,
        "coverage": len(judged) / len(results) if results else 0.0,
        "position_bias": (sum(first_pos_mass) / len(first_pos_mass)) if first_pos_mass else None,
        "n": len(results),
    }


# --- vérifications des tâches génératives -----------------------------------

def check_numeric(text, value):
    """Vrai si la valeur attendue apparaît comme nombre autonome au début de la réponse."""
    head = text[:280]
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", head)
    if not nums:
        return False, "aucun nombre"
    target = float(value)
    found = [float(n.replace(",", ".")) for n in nums]
    ok = any(abs(v - target) < 1e-6 for v in found)
    return ok, f"nombres trouvés={found[:6]} (attendu {value})"


def check_words_max(text, value, keywords):
    words = re.findall(r"[\wÀ-ÿ'-]+", text)
    ok_len = len(words) <= value
    low = text.lower()
    missing = [k for k in (keywords or []) if k.lower() not in low]
    return ok_len and not missing, f"{len(words)} mots (max {value}), manquants={missing}"


def _extract_json(text):
    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def check_json(text, keys, types):
    raw = _extract_json(text)
    if raw is None:
        return False, "aucun objet JSON"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"JSON invalide: {exc}"
    missing = [k for k in keys if k not in obj]
    bad = []
    for k, t in (types or {}).items():
        if t == "int" and not isinstance(obj.get(k), int):
            bad.append(f"{k} n'est pas un int")
        if t == "list" and not isinstance(obj.get(k), list):
            bad.append(f"{k} n'est pas une liste")
    return not missing and not bad, f"manquants={missing} types_ko={bad}"


def check_python(text, tests):
    blocks = re.findall(r"```(?:python)?\s*(.*?)```", text, flags=re.S)
    code = (max(blocks, key=len) if blocks else text).strip()
    if "def " not in code:
        return False, "aucune fonction détectée"
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "candidate.py"
        path.write_text(code + "\n\n" + tests + "\n", encoding="utf-8")
        try:
            proc = subprocess.run([sys.executable, str(path)], capture_output=True,
                                  text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return False, "timeout (>20 s)"
        ok = proc.returncode == 0
        detail = "tests OK" if ok else (proc.stderr or proc.stdout).strip().splitlines()[-1][:200] if (proc.stderr or proc.stdout) else "échec"
        return ok, detail


def check_bullets(text, count, max_words):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    bullets = [ln for ln in lines if re.match(r"^[-•*]\s+", ln)]
    too_long = [ln for ln in bullets
                if len(re.findall(r"[\wÀ-ÿ'-]+", re.sub(r"^[-•*]\s+", "", ln))) > max_words]
    extra = [ln for ln in lines if ln not in bullets]
    ok = len(bullets) == count and not too_long and not extra
    return ok, f"{len(bullets)} puces (attendu {count}), trop longues={len(too_long)}, hors-puces={len(extra)}"


def check_keywords(text, keywords):
    low = text.lower()
    missing = [k for k in keywords if k.lower() not in low]
    return not missing, f"manquants={missing}"


def run_gen(srv, tasks, defaults, timeout, raw_dir, progress=None):
    raw_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for ti, task in enumerate(tasks, 1):
        msgs = [{"role": "system", "content": defaults["gen_system"]},
                {"role": "user", "content": task["prompt"]}]
        text, timings, err = None, {}, None
        for kwargs in ({"enable_thinking": False}, None):
            try:
                body = {
                    "messages": msgs, "max_tokens": task.get("max_tokens", 300),
                    "temperature": 0, "top_k": 1,
                }
                if kwargs:
                    body["chat_template_kwargs"] = kwargs
                r = srv.post("/v1/chat/completions", body, timeout=timeout)
                text = (r["choices"][0]["message"].get("content") or "")
                timings = r.get("timings") or {}
                break
            except Exception as exc:  # noqa: BLE001
                err = str(exc)[:300]
                if kwargs is None:
                    text = None
        if text is None:
            results.append({"id": task["id"], "passed": False, "detail": err or "pas de réponse"})
            if progress:
                progress(ti, len(tasks))
            continue

        cleaned = strip_think(text)
        check = task.get("check") or {"type": "manual"}
        try:
            ctype = check["type"]
            if ctype == "numeric":
                passed, detail = check_numeric(cleaned, check["value"])
            elif ctype == "words_max":
                passed, detail = check_words_max(cleaned, check["value"], check.get("keywords"))
            elif ctype == "json":
                passed, detail = check_json(cleaned, check["keys"], check.get("types"))
            elif ctype == "python_test":
                passed, detail = check_python(cleaned, check["code"])
            elif ctype == "bullets":
                passed, detail = check_bullets(cleaned, check["count"], check["max_words"])
            elif ctype == "keywords":
                passed, detail = check_keywords(cleaned, check["value"])
            else:
                passed, detail = None, "à relire manuellement"
        except Exception as exc:  # noqa: BLE001
            passed, detail = False, f"erreur de vérification: {exc}"

        (raw_dir / f"{task['id']}.md").write_text(
            f"# {task['id']} ({task.get('cat')})\n\n"
            f"## Consigne\n{task['prompt']}\n\n## Réponse brute\n{text}\n\n"
            f"## Vérification\nauto={passed} — {detail}\n", encoding="utf-8")
        results.append({
            "id": task["id"], "cat": task.get("cat"), "passed": passed,
            "detail": detail, "chars": len(text),
            "tok_s": timings.get("predicted_per_second"),
        })
        if progress:
            progress(ti, len(tasks))

    auto = [r["passed"] for r in results if r["passed"] is not None]
    return {
        "tasks": results,
        "score": (sum(1 for p in auto if p) / len(auto)) if auto else None,
        "n": len(results),
    }


# ----------------------------------------------------------------------------
# Boucle principale
# ----------------------------------------------------------------------------

def progress_printer(prefix):
    """Renvoie un callback qui loggue tous les 10 % et met à jour l'état web."""
    last = [0]

    def cb(done, total):
        set_state(detail=f"{done}/{total}")
        pct = int(100 * done / total) if total else 100
        if pct >= last[0] + 10 or done == total:
            last[0] = pct
            log(f"    {prefix} {done}/{total} ({pct} %)")
    return cb


def bench_model(path, args, eval_set, server_flags, idx=1, total=1):
    name = Path(path).name
    meta = GGUFMeta(path)
    size_bytes = os.path.getsize(path)
    avail = mem_available_gib()

    ctx = args.ctx
    need = size_bytes / 2**30 + meta.kv_cache_bytes(ctx) / 2**30 + 0.45
    # garde-fou : si ça ne tient pas, on réduit le contexte (jamais le ctx natif)
    if need > avail * 0.95 and ctx > 2048:
        ctx = 2048
        need = size_bytes / 2**30 + meta.kv_cache_bytes(ctx) / 2**30 + 0.45
    if need > avail * 0.95:
        log(f"    IGNORÉ : RAM insuffisante (besoin ≈ {need:.1f} Gio, dispo {avail:.1f} Gio)")
        return {
            "model": name, "status": "skipped",
            "error": f"RAM insuffisante : besoin ≈ {need:.1f} Gio, dispo {avail:.1f} Gio",
        }

    row = {
        "model": name,
        "path": str(path),
        "arch": meta.kv.get("general.architecture"),
        "params_b": round(meta.params / 1e9, 2) if meta.params else None,
        "quant": meta.quant,
        "size_gb": round(size_bytes / 1e9, 2),
        "ctx": ctx,
        "threads": args.threads,
        "ram_avail_gib": round(avail, 2),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    log(f"    {meta.kv.get('general.architecture')} · {meta.quant} · "
        f"{meta.params / 1e9:.2f} B · {size_bytes / 1e9:.2f} Go · ctx {ctx}")

    srv = Server(args.server, path, ctx, args.threads,
                 Path(args.out) / "logs" / (Path(name).stem + ".log"),
                 extra_flags=server_flags)
    try:
        set_state(phase="chargement", model=name, index=idx, total=total,
                  detail="llama-server démarre…", ctx=ctx)
        row["load_s"] = round(srv.start(), 1)
        props = srv.props()
        row["n_ctx_server"] = (props.get("default_generation_settings") or {}).get("n_ctx")
        log(f"    serveur prêt en {row['load_s']} s (n_ctx={row['n_ctx_server']})")

        if args.mode in ("all", "speed"):
            stream_prompt = TG_PROMPT
            try:
                msgs = [{"role": "user", "content": TG_PROMPT}]
                p, _ = srv.apply_template(msgs, {"enable_thinking": False})
                if p:
                    stream_prompt = p
            except Exception:  # noqa: BLE001
                pass
            set_state(phase="vitesse", detail="")
            speed = measure_speed(srv, args.reps, stream_prompt, args.timeout,
                                  progress=lambda m: set_state(detail=m))
            row.update({k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in speed.items() if not k.endswith("_raw")})
            row["pp_raw"] = [round(v, 1) for v in speed["pp_raw"]]
            row["tg_raw"] = [round(v, 1) for v in speed["tg_raw"]]
            log(f"    vitesse : préfill {row['pp_tok_s']:.1f} tok/s · "
                f"génération {row['tg_tok_s']:.1f} tok/s · TTFT {row['ttft_ms']:.0f} ms")

        if args.mode in ("all", "eval"):
            stats = {}
            set_state(phase="QCM", detail="0/%d" % (len(eval_set["mcq"]) * args.rotations))
            log(f"    QCM : {len(eval_set['mcq'])} questions × {args.rotations} rotations")
            mcq = run_mcq(srv, eval_set["mcq"], eval_set["defaults"], args.timeout,
                          stats, rotations=args.rotations,
                          progress=progress_printer("QCM"))
            set_state(phase="gén.", detail="0/%d" % len(eval_set["gen"]))
            log(f"    génératif : {len(eval_set['gen'])} tâches")
            gen = run_gen(srv, eval_set["gen"], eval_set["defaults"], args.timeout,
                          Path(args.out) / "raw" / Path(name).stem,
                          progress=progress_printer("gén."))
            row["mcq_soft"] = round(mcq["soft_acc"], 3) if mcq["soft_acc"] is not None else None
            row["mcq_acc"] = round(mcq["acc"], 3) if mcq["acc"] is not None else None
            row["mcq_coverage"] = round(mcq["coverage"], 3)
            row["mcq_bias"] = round(mcq["position_bias"], 3) if mcq["position_bias"] is not None else None
            row["gen_score"] = round(gen["score"], 3) if gen["score"] is not None else None
            row["stats"] = stats
            raw = Path(args.out) / "raw" / Path(name).stem
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "mcq.json").write_text(
                json.dumps(mcq["items"], ensure_ascii=False, indent=1), encoding="utf-8")
            row["_mcq_items"] = mcq["items"]
            row["_gen_items"] = gen["tasks"]
            log(f"    qualité : QCM {row['mcq_soft']:.0%} (juste {row['mcq_acc']:.0%}) · "
                f"génératif {row['gen_score']:.0%}")

        row["status"] = "ok"
        set_state(phase="terminé", detail="")
    except Exception as exc:  # noqa: BLE001
        row["status"] = "failed"
        row["error"] = f"{type(exc).__name__}: {exc}"[:600]
        row["log_tail"] = srv.log_tail(1200)
        log(f"    ÉCHEC : {row['error'].splitlines()[0][:160]}")
    finally:
        srv.stop()
    return row


def llama_version(binary):
    p = Path(binary).resolve()
    for parent in p.parents:
        if (parent / ".git").exists():
            try:
                out = subprocess.run(
                    ["git", "-C", str(parent), "describe", "--tags", "--always"],
                    capture_output=True, text=True, timeout=10)
                if out.returncode == 0:
                    return out.stdout.strip()
            except Exception:  # noqa: BLE001
                break
    return "version inconnue"


def write_outputs(out_dir, rows):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8")

    cols = ["model", "arch", "params_b", "quant", "size_gb", "ctx", "n_ctx_server",
            "threads", "load_s", "pp_tok_s", "pp_prompt_n", "tg_tok_s", "tg_tokens",
            "ttft_ms", "stream_tok_s", "mcq_soft", "mcq_acc", "mcq_coverage",
            "mcq_bias", "gen_score", "status", "error", "ts"]
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    def f(v, nd=1):
        return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))

    def pct(v):
        return "—" if v is None else f"{v * 100:.0f} %"

    ok = [r for r in rows if r.get("status") == "ok"]
    ok.sort(key=lambda r: (r.get("mcq_soft") or -1, r.get("tg_tok_s") or 0), reverse=True)
    lines = ["# Résultats du benchmark", "",
             f"Machine : {os.cpu_count()} threads, {mem_available_gib():.1f} Gio dispo, "
             f"llama.cpp `{llama_version(server_bin)}`", ""]
    if ok:
        lines += ["## Classement (qualité puis vitesse)", "",
                  "| Modèle | Params | Quant | Taille | ctx | pp tok/s | tg tok/s | TTFT | QCM (soft) | QCM (juste) | biais pos. | Gén. |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in ok:
            lines.append(
                f"| {r['model']} | {f(r.get('params_b'), 2)} B | {r.get('quant')} | "
                f"{f(r.get('size_gb'), 2)} Go | {r.get('ctx')} | {f(r.get('pp_tok_s'))} | "
                f"{f(r.get('tg_tok_s'))} | {f(r.get('ttft_ms'), 0)} ms | "
                f"{pct(r.get('mcq_soft'))} | {pct(r.get('mcq_acc'))} | "
                f"{f(r.get('mcq_bias'), 2)} | {pct(r.get('gen_score'))} |")
        lines += [""]
    bad = [r for r in rows if r.get("status") != "ok"]
    if bad:
        lines += ["## Non mesurés", "", "| Modèle | Statut | Détail |", "|---|---|---|"]
        for r in bad:
            lines.append(f"| {r['model']} | {r['status']} | {(r.get('error') or '').splitlines()[0][:160]} |")
        lines += [""]
    lines += ["## Rappel méthodo", "",
              "- `pp` = préfill (tok/s d'ingestion du prompt), `tg` = génération (tok/s).",
              "- 3 répétitions, médiane ; `temperature 0`, même `ctx` et même `-t` pour tous.",
              "- QCM : 40 questions FR/EN, chaque question posée 4 fois avec les choix décalés",
              "  (le biais de position est ainsi neutralisé) ; « soft » = probabilité moyenne",
              "  accordée à la bonne réponse, « juste » = choix le plus probable.",
              "- `biais pos.` = probabilité moyenne donnée à la première proposition (0,25 = neutre).",
              "- Gén. : 6 tâches, auto-scoring ; les réponses brutes sont dans `raw/`.",
              ""]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


# ----------------------------------------------------------------------------
# Tableau de bord web (stdlib uniquement)
# ----------------------------------------------------------------------------

CSS = """
:root{--bg:#0f1115;--fg:#e7e9ee;--muted:#9aa3b2;--card:#171a21;--line:#262b36;
--acc:#4da3ff;--ok:#3ecf8e;--warn:#f5a524;--bad:#f31260}
@media (prefers-color-scheme:light){:root{--bg:#f7f8fa;--fg:#15181d;--muted:#5b6472;
--card:#fff;--line:#e3e6ec;--acc:#0b6bcb;--ok:#0f9d58;--warn:#b26a00;--bad:#c62828}}
*{box-sizing:border-box}body{margin:0;padding:24px;max-width:1200px;margin:auto;
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--fg)}
h1{font-size:20px;margin:0 0 2px}.sub{color:var(--muted);font-size:13px;font-weight:400}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
td:first-child,th:first-child{text-align:left;white-space:normal}
.bar{display:inline-block;width:64px;height:6px;border-radius:3px;background:var(--line);
vertical-align:middle;margin-right:6px;overflow:hidden}.bar>i{display:block;height:100%}
.prog{height:8px;background:var(--line);border-radius:4px;overflow:hidden;margin:10px 0}
.prog>i{display:block;height:100%;background:var(--acc);transition:width .4s}
pre{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;
overflow:auto;max-height:240px;font-size:12px;margin:0}
details{margin:6px 0}summary{cursor:pointer;color:var(--acc)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
.k{color:var(--muted);font-size:12px}.big{font-size:22px;font-weight:600}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
"""


def _color(v):
    if v is None:
        return "var(--muted)"
    return "var(--ok)" if v >= 0.7 else ("var(--warn)" if v >= 0.4 else "var(--bad)")


def _bar(v, width=64):
    if v is None:
        return "<span class='k'>—</span>"
    pct = max(0.0, min(1.0, float(v))) * 100
    return (f"<span class='bar' style='width:{width}px'><i style='width:{pct:.0f}%;"
            f"background:{_color(v)}'></i></span>{pct:.0f} %")


def _num(v, nd=1, unit=""):
    if v is None:
        return "<span class='k'>—</span>"
    return f"{v:.{nd}f}{unit}"


def read_state(out_dir):
    p = Path(out_dir) / "state.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def read_rows(out_dir):
    p = Path(out_dir) / "results.jsonl"
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def log_tail(out_dir, n=80):
    p = Path(out_dir) / "run.log"
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def status_fragment(out_dir):
    st = read_state(out_dir)
    if not st:
        return ("<div class='card'><span class='k'>Aucune exécution en cours. "
                "Lancez le benchmark pour voir la progression ici.</span></div>")
    phase = st.get("phase", "?")
    model = st.get("model", "—")
    idx, total = st.get("index"), st.get("total")
    detail = st.get("detail", "")
    pct = None
    m = re.match(r"^(\d+)/(\d+)$", str(detail))
    if m and int(m.group(2)):
        pct = 100 * int(m.group(1)) / int(m.group(2))
    running = phase not in ("terminé", "fini", "au repos", "", None)
    badge = ("<span class='tag' style='border-color:var(--ok);color:var(--ok)'>en cours</span>"
             if running else "<span class='tag'>au repos</span>")
    prog = (f"<div class='prog'><i style='width:{pct:.0f}%'></i></div>" if pct is not None else "")
    head = f"Exécution {idx}/{total}" if idx else "Aucune mesure en cours"
    title = model or "—"
    return (f"<div class='card'><div class='k'>{head} {badge}</div>"
            f"<div class='big'>{html.escape(str(title))}</div>"
            f"<div class='k'>{html.escape(str(phase))} · {html.escape(str(detail))}"
            f"{' · ctx ' + str(st.get('ctx')) if st.get('ctx') else ''}</div>{prog}</div>")


def results_fragment(out_dir):
    rows = read_rows(out_dir)
    if not rows:
        return "<div class='card'><span class='k'>Aucun résultat pour l'instant.</span></div>"
    ok = [r for r in rows if r.get("status") == "ok"]
    ok.sort(key=lambda r: (r.get("mcq_soft") or -1, r.get("tg_tok_s") or 0), reverse=True)
    out = ["<div class='card'><table><thead><tr>"
           "<th>Modèle</th><th>Params</th><th>Quant</th><th>ctx</th>"
           "<th>préfill</th><th>génération</th><th>TTFT</th><th>QCM</th><th>juste</th>"
           "<th>génératif</th></tr></thead><tbody>"]
    for r in ok:
        name = html.escape(str(r.get("model")))
        out.append(
            f"<tr><td>{name}</td>"
            f"<td>{_num(r.get('params_b'), 2)} B</td>"
            f"<td>{html.escape(str(r.get('quant')))}</td>"
            f"<td>{r.get('ctx')}</td>"
            f"<td>{_num(r.get('pp_tok_s'))} tok/s</td>"
            f"<td><b>{_num(r.get('tg_tok_s'))}</b> tok/s</td>"
            f"<td>{_num(r.get('ttft_ms'), 0)} ms</td>"
            f"<td>{_bar(r.get('mcq_soft'))}</td>"
            f"<td>{_bar(r.get('mcq_acc'))}</td>"
            f"<td>{_bar(r.get('gen_score'))}</td></tr>")
    out.append("</tbody></table>")

    # diagnostic détaillé, modèle par modèle
    for r in ok:
        name = html.escape(str(r.get("model")))
        stem = Path(str(r.get("model"))).stem
        cats = {}
        for it in (r.get("_mcq_items") or []):
            cats.setdefault(it.get("cat") or "?", []).append(it.get("soft") or 0.0)
        cat_html = "".join(
            f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
            f"<span class='k' style='width:90px;display:inline-block'>{html.escape(k)}</span>"
            f"{_bar(sum(v) / len(v), 110)}</div>"
            for k, v in sorted(cats.items()))
        gen_html = "".join(
            f"<div>{'✅' if g.get('passed') else ('⚠️' if g.get('passed') is None else '❌')} "
            f"<span class='k'>{html.escape(str(g.get('id')))}</span> — "
            f"{html.escape(str(g.get('detail'))[:110])}</div>"
            for g in (r.get("_gen_items") or []))
        files = ""
        raw_dir = Path(out_dir) / "raw" / stem
        if raw_dir.exists():
            files = " · ".join(
                f"<a href='/raw/{stem}/{html.escape(f.name)}'>{html.escape(f.name)}</a>"
                for f in sorted(raw_dir.iterdir()) if f.is_file())
        out.append(
            f"<details><summary>{name} — détail</summary>"
            f"<div class='grid'><div><div class='k'>QCM par catégorie</div>{cat_html or '—'}</div>"
            f"<div><div class='k'>Tâches génératives</div>{gen_html or '—'}</div></div>"
            f"<div class='k' style='margin-top:8px'>biais de position : "
            f"{_num(r.get('mcq_bias'), 2)} · couverture : {_num(r.get('mcq_coverage'), 2)}</div>"
            f"<div class='k' style='margin-top:6px'>fichiers : {files or '—'}</div></details>")
    out.append("</div>")
    return "".join(out)


def render_page(out_dir):
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bench GGUF</title><style>{CSS}</style></head><body>
<h1>Benchmark GGUF <span class="sub">llama.cpp · CPU</span></h1>
<div id="status">{status_fragment(out_dir)}</div>
<div id="results">{results_fragment(out_dir)}</div>
<div class="card"><div class="k" style="margin-bottom:8px">journal d'exécution</div>
<pre id="log">{html.escape(log_tail(out_dir))}</pre></div>
<script>
const keep = () => [...document.querySelectorAll('details')].map(d => d.open);
const restore = (s) => document.querySelectorAll('details').forEach((d, i) => d.open = !!s[i]);
async function tick() {{
  try {{
    const open = keep();
    const r = await fetch('/api/state', {{cache: 'no-store'}});
    const j = await r.json();
    document.getElementById('status').innerHTML = j.status;
    document.getElementById('results').innerHTML = j.results;
    document.getElementById('log').textContent = j.log;
    restore(open);
  }} catch (e) {{}}
}}
setInterval(tick, 3000);
</script></body></html>"""


def make_handler(out_dir):
    root = Path(out_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def do_GET(self):  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path == "/":
                self._send(200, render_page(out_dir))
            elif path == "/api/state":
                payload = {
                    "status": status_fragment(out_dir),
                    "results": results_fragment(out_dir),
                    "log": log_tail(out_dir),
                }
                self._send(200, json.dumps(payload, ensure_ascii=False), "application/json")
            elif path == "/results.csv":
                f = root / "results.csv"
                if f.exists():
                    self._send(200, f.read_bytes(), "text/csv; charset=utf-8")
                else:
                    self._send(404, "pas encore de résultats")
            elif path.startswith("/raw/"):
                f = (root / path.lstrip("/")).resolve()
                if str(f).startswith(str(root)) and f.is_file():
                    ctype = "text/plain; charset=utf-8"
                    if f.suffix == ".json":
                        ctype = "application/json; charset=utf-8"
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, "fichier introuvable")
            else:
                self._send(404, "introuvable")

        def log_message(self, *args):  # silence
            pass

    return Handler


def start_dashboard(out_dir, port):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(out_dir))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def print_table(rows):
    ok = [r for r in rows if r.get("status") == "ok"]
    ok.sort(key=lambda r: (r.get("mcq_soft") or -1), reverse=True)
    if not ok:
        return
    log("")
    log(f"{'modèle':34s} {'params':>7s} {'ctx':>5s} {'pp t/s':>7s} {'tg t/s':>7s} "
        f"{'QCM':>5s} {'juste':>6s} {'gén':>5s}")
    log("-" * 84)
    for r in ok:
        name = str(r.get("model"))[:34]
        log(f"{name:34s} {r.get('params_b') or 0:6.2f}B {r.get('ctx') or 0:5d} "
            f"{r.get('pp_tok_s') or 0:7.1f} {r.get('tg_tok_s') or 0:7.1f} "
            f"{(r.get('mcq_soft') or 0) * 100:4.0f}% {(r.get('mcq_acc') or 0) * 100:5.0f}% "
            f"{(r.get('gen_score') or 0) * 100:4.0f}%")


def main():
    global server_bin, _LOG_PATH
    ap = argparse.ArgumentParser(description="Benchmark de modèles .gguf avec llama.cpp")
    ap.add_argument("--models", default="/root/models", help="dossier ou fichiers .gguf")
    ap.add_argument("--out", default="/root/bench/results")
    ap.add_argument("--server", default="/root/llama.cpp/build/bin/llama-server")
    ap.add_argument("--eval-set", default="/root/bench/eval_set.json")
    ap.add_argument("--ctx", type=int, default=4096,
                    help="contexte alloué à chaque modèle (2048 ou 4096 suffisent)")
    ap.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--rotations", type=int, default=4,
                    help="permutations des choix au QCM (4 = biais de position neutralisé)")
    ap.add_argument("--timeout", type=int, default=900, help="timeout par requête (s)")
    ap.add_argument("--mode", choices=["all", "speed", "eval"], default="all")
    ap.add_argument("--force", action="store_true", help="refaire les modèles déjà mesurés")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--list", action="store_true", help="lister les modèles et quitter")
    ap.add_argument("--serve", type=int, default=0, metavar="PORT",
                    help="tableau de bord web pendant l'exécution (ex : 8080)")
    ap.add_argument("--serve-only", type=int, default=0, metavar="PORT",
                    help="servir les résultats existants sans rien mesurer")
    ap.add_argument("--stop", type=int, default=0, metavar="PORT",
                    help="arrêter le tableau de bord qui écoute sur ce port, puis quitter")
    args = ap.parse_args()

    if args.stop:
        sys.exit(stop_server(args.stop))

    server_bin = args.server

    src = Path(args.models)
    if src.is_dir():
        models = sorted(p for p in src.iterdir() if p.suffix.lower() == ".gguf")
    else:
        models = [src]
    if args.limit:
        models = models[:args.limit]

    if args.list:
        for p in models:
            m = GGUFMeta(p)
            print(f"{p.name:50s} {os.path.getsize(p)/1e9:5.2f} Go  "
                  f"{m.kv.get('general.architecture')}  {m.quant}  "
                  f"{m.params/1e9:.2f} B  ctx natif={m.context_length}")
        return

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    STATE["_path"] = str(out_dir / "state.json")
    _LOG_PATH = str(out_dir / "run.log")

    if args.serve_only:
        start_dashboard(out_dir, args.serve_only)
        set_state(phase="au repos", detail="", model="")
        log(f"Tableau de bord : http://{local_ip()}:{args.serve_only}/  (Ctrl-C pour quitter)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return

    if not Path(server_bin).exists():
        sys.exit(f"llama-server introuvable : {server_bin}")
    if not models:
        sys.exit(f"aucun .gguf dans {src}")

    open(_LOG_PATH, "w", encoding="utf-8").close()
    eval_set = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))

    help_txt = server_help(server_bin)
    base_flags = []
    if "--jinja" in help_txt:
        base_flags.append("--jinja")

    done = set()
    jsonl = out_dir / "results.jsonl"
    rows = []
    if jsonl.exists() and not args.force:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
                rows.append(e)
                if e.get("status") == "ok":
                    done.add(e["model"])
            except json.JSONDecodeError:
                pass

    if args.serve:
        start_dashboard(out_dir, args.serve)
        log(f"Tableau de bord : http://{local_ip()}:{args.serve}/")

    log(f"llama.cpp    : {server_bin} ({llama_version(server_bin)})")
    log(f"Modèles      : {len(models)} dans {src} ({len(done)} déjà mesurés)")
    log(f"Réglages     : ctx {args.ctx} · threads {args.threads} · reps {args.reps} · "
        f"{args.rotations} rotations QCM")
    log(f"RAM dispo    : {mem_available_gib():.1f} Gio")

    def on_sigint(signum, frame):  # noqa: ARG001
        log("Interruption : écriture des résultats partiels…")
        write_outputs(out_dir, rows)
        sys.exit(130)

    signal.signal(signal.SIGINT, on_sigint)

    for i, path in enumerate(models, 1):
        if path.name in done:
            log(f"[{i}/{len(models)}] {path.name} — déjà mesuré, ignoré (--force pour refaire)")
            continue
        log(f"[{i}/{len(models)}] {path.name}")
        set_state(model=path.name, index=i, total=len(models), phase="démarrage",
                  detail="", started=time.time())
        t0 = time.time()
        row = bench_model(path, args, eval_set, base_flags, i, len(models))
        row["elapsed_s"] = round(time.time() - t0, 1)
        rows = [r for r in rows if r.get("model") != row["model"]] + [row]
        write_outputs(out_dir, rows)
        if row["status"] == "ok":
            log(f"    terminé en {row['elapsed_s']:.0f} s")
        else:
            log(f"    {row['status'].upper()} : {str(row.get('error', '')).splitlines()[0][:160]}")
        set_state(phase="terminé", detail="", index=i, total=len(models))
        time.sleep(2)  # laisser le système récupérer la RAM

    write_outputs(out_dir, rows)
    print_table(rows)
    log(f"Terminé. Détail : {out_dir}/summary.md · CSV : {out_dir}/results.csv")
    if args.serve:
        log(f"Tableau de bord toujours actif sur le port {args.serve} (Ctrl-C pour quitter)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    server_bin = ""
    main()
