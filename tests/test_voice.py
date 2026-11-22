"""Unit tests for the local Voice Daemon helpers (PCM RMS, WAV writing, TTS detection)."""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from jarvis.voice import VoiceConfig, compute_pcm16_rms, detect_tts_backend, write_wav


def test_pcm16_rms_and_wav_roundtrip(tmp_path: Path) -> None:
    silent = struct.pack("<100h", *([0] * 100))
    assert compute_pcm16_rms(silent) == 0.0

    loud = struct.pack("<100h", *([1000] * 100))
    assert abs(compute_pcm16_rms(loud) - 1000.0) < 0.01

    wav_path = write_wav(tmp_path / "test.wav", loud)
    assert wav_path.exists()
    with wave.open(str(wav_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 100


def test_tts_backend_detection_returns_valid_value() -> None:
    backend = detect_tts_backend()
    assert backend in ("piper", "espeak-ng", "espeak", "spd-say", "none")
    cfg = VoiceConfig()
    assert cfg.wake_word == "hey_jarvis"
