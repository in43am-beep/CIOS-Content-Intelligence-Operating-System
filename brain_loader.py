"""Loads brain markdown files and composes feature-specific system prompts.

The brain/ folder holds the Viral Video Brain (12 system files + main brain).
Each API feature pulls only the files it needs, so prompts stay focused.
"""
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent / "brain"

BASE_PERSONA = """You are CIOS — Content Intelligence Operating System for YouTube creators.
You turn audience psychology, packaging science, and scripting systems into
practical outputs. Rules:
- Jawab SAADA ROMAN URDU me do (English terms jahan natural hon wahan rehne do).
- Concrete, actionable output do — vague motivation nahi.
- Jab JSON manga jaye to SIRF valid JSON do, koi extra text nahi.
"""

_loaded: dict[str, str] = {}


def load_brain(*rel_paths: str) -> str:
    parts = []
    for rel in rel_paths:
        if rel not in _loaded:
            p = BRAIN_DIR / rel
            _loaded[rel] = p.read_text(encoding="utf-8") if p.exists() else ""
        if _loaded[rel]:
            parts.append(f"--- KNOWLEDGE: {rel} ---\n{_loaded[rel]}")
    return "\n\n".join(parts)


def system_for(*files: str, extra: str = "") -> str:
    prompt = BASE_PERSONA + "\n\nTUMHARA KNOWLEDGE BASE (is system ko follow karo):\n" + load_brain(*files)
    if extra:
        prompt += "\n\n" + extra
    return prompt
