"""100% free, local Hands-Free Voice Daemon for Jarvis.

Pipeline:
  1. Wake word: openWakeWord ("hey_jarvis") — or push-to-talk (--push-to-talk)
  2. Speech-to-Text: faster-whisper (local CPU int8, zero cloud cost)
  3. Brain: JarvisOrchestrator (in-process) or Core Service HTTP API (/api/chat)
  4. Text-to-Speech: local Piper / espeak-ng / spd-say (zero cloud cost)
"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import Settings
from jarvis.orchestrator import JarvisOrchestrator

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SAMPLES = 1280  # 80ms frame (required by openWakeWord at 16kHz)


@dataclass(frozen=True)
class VoiceConfig:
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    whisper_model: str = "base.en"
    silence_rms: float = 350.0
    silence_seconds: float = 1.2
    max_record_seconds: float = 15.0
    piper_model_path: str = ""
    api_url: str = ""  # Optional: e.g. http://127.0.0.1:8765 to talk to running Core Service


def compute_pcm16_rms(pcm_bytes: bytes) -> float:
    """Compute RMS amplitude of signed 16-bit little-endian PCM audio."""
    n_samples = len(pcm_bytes) // 2
    if n_samples == 0:
        return 0.0
    samples = struct.unpack(f"<{n_samples}h", pcm_bytes[: n_samples * 2])
    mean_sq = sum(s * s for s in samples) / n_samples
    return math.sqrt(mean_sq)


def write_wav(path: Path, pcm_bytes: bytes, sample_rate: int = SAMPLE_RATE) -> Path:
    """Write mono 16-bit PCM bytes to a WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return path


def detect_tts_backend(piper_model_path: str = "") -> str:
    """Detect the best available 100% free local TTS backend."""
    if piper_model_path and Path(piper_model_path).exists() and shutil.which("piper"):
        return "piper"
    if shutil.which("espeak-ng"):
        return "espeak-ng"
    if shutil.which("espeak"):
        return "espeak"
    if shutil.which("spd-say"):
        return "spd-say"
    return "none"


def speak_local(text: str, piper_model_path: str = "") -> str:
    """Speak text using the best available local free TTS backend. Returns backend name."""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "none"

    backend = detect_tts_backend(piper_model_path)
    if backend == "piper":
        player = shutil.which("aplay") or shutil.which("paplay")
        if player:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                subprocess.run(
                    ["piper", "--model", piper_model_path, "--output_file", tmp.name],
                    input=cleaned.encode("utf-8"),
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run([player, tmp.name], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "piper"

    if backend in ("espeak-ng", "espeak"):
        subprocess.run([backend, "-s", "165", cleaned], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return backend

    if backend == "spd-say":
        subprocess.run(["spd-say", cleaned], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return backend

    return "none"


def _send_to_core_api(api_url: str, text: str) -> str:
    """Send a transcribed utterance to a running Jarvis Core Service (/api/chat)."""
    url = api_url.rstrip("/") + "/api/chat"
    payload = json.dumps({"message": text, "channel": "voice"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("reply", "")).strip()


class VoiceDaemon:
    """Hands-free local voice loop for Jarvis."""

    def __init__(
        self,
        orchestrator: JarvisOrchestrator | None = None,
        config: VoiceConfig | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.config = config or VoiceConfig()
        self.orchestrator = orchestrator or (None if self.config.api_url else JarvisOrchestrator(settings or Settings.load()))
        self._whisper = None

    def _get_whisper(self):
        if self._whisper is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError(
                    "Voice STT requires `faster-whisper`. "
                    "Install with: uv pip install --python .venv/bin/python -e '.[voice]'"
                ) from exc
            self._whisper = WhisperModel(self.config.whisper_model, device="cpu", compute_type="int8")
        return self._whisper

    def transcribe(self, wav_path: Path) -> str:
        model = self._get_whisper()
        segments, _ = model.transcribe(str(wav_path))
        return " ".join(seg.text.strip() for seg in segments).strip()

    def _record_utterance(self) -> bytes:
        """Record from the default microphone until trailing silence is detected."""
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Microphone capture requires `sounddevice` and `numpy`. "
                "Install with: uv pip install --python .venv/bin/python -e '.[voice]'"
            ) from exc

        frames: list[bytes] = []
        heard_speech = False
        silent_chunks = 0
        max_chunks = int((self.config.max_record_seconds * SAMPLE_RATE) / CHUNK_SAMPLES)
        silence_limit = max(1, int((self.config.silence_seconds * SAMPLE_RATE) / CHUNK_SAMPLES))

        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SAMPLES) as stream:
            for _ in range(max_chunks):
                data, _ = stream.read(CHUNK_SAMPLES)
                raw = np.asarray(data, dtype=np.int16).tobytes()
                frames.append(raw)
                rms = compute_pcm16_rms(raw)
                if rms >= self.config.silence_rms:
                    heard_speech = True
                    silent_chunks = 0
                elif heard_speech:
                    silent_chunks += 1
                    if silent_chunks >= silence_limit:
                        break
        return b"".join(frames)

    def _wait_for_wake_word(self) -> None:
        """Listen continuously for the local openWakeWord trigger ('hey_jarvis')."""
        try:
            import numpy as np
            import sounddevice as sd
            from openwakeword.model import Model as WakeModel
        except ImportError as exc:
            raise RuntimeError(
                "Wake-word detection requires `openwakeword`, `sounddevice`, and `numpy`. "
                "Install with: uv pip install --python .venv/bin/python -e '.[voice]'"
            ) from exc

        wake_model = WakeModel(wakeword_models=[self.config.wake_word], inference_framework="onnx")
        print(f"Listening for wake word '{self.config.wake_word}' (Ctrl+C to stop)...")
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=CHUNK_SAMPLES) as stream:
            while True:
                data, _ = stream.read(CHUNK_SAMPLES)
                audio = np.asarray(data, dtype=np.int16).reshape(-1)
                scores = wake_model.predict(audio)
                if any(score >= self.config.wake_threshold for score in scores.values()):
                    wake_model.reset()
                    return

    async def handle_utterance(self, transcript: str) -> str:
        if self.config.api_url:
            return await asyncio.to_thread(_send_to_core_api, self.config.api_url, transcript)
        assert self.orchestrator is not None
        return await self.orchestrator.chat(transcript, channel="voice")

    async def run(self, push_to_talk: bool = False) -> int:
        tts_backend = detect_tts_backend(self.config.piper_model_path)
        print(f"Jarvis Voice Daemon starting (STT: faster-whisper/{self.config.whisper_model}, TTS: {tts_backend}).")
        while True:
            try:
                if push_to_talk:
                    await asyncio.to_thread(input, "\nPress Enter and speak (Ctrl+C to exit)... ")
                else:
                    await asyncio.to_thread(self._wait_for_wake_word)
                    print("Wake word detected! Listening...")

                pcm = await asyncio.to_thread(self._record_utterance)
                with tempfile.TemporaryDirectory() as tmpdir:
                    wav_path = write_wav(Path(tmpdir) / "utterance.wav", pcm)
                    transcript = await asyncio.to_thread(self.transcribe, wav_path)

                if not transcript:
                    print("[voice] No speech detected.")
                    continue

                print(f"you(voice)> {transcript}")
                reply = await self.handle_utterance(transcript)
                print(f"jarvis> {reply}")
                await asyncio.to_thread(speak_local, reply, self.config.piper_model_path)
            except (KeyboardInterrupt, EOFError):
                print("\nVoice daemon stopped.")
                return 0
            except RuntimeError as exc:
                sys.stderr.write(f"[voice error] {exc}\n")
                return 1
