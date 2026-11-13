"""
utilidades_seguridad.py
───────────────────────────────────────────────────────────────────────────────
HOTFIX DEFINITIVO (PUNTO 49): Conversiones numéricas seguras y blindaje contra
None en diccionarios de estadísticas, métricas de períodos y patrones.
───────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import builtins as _builtins
from typing import Any

try:
    import numpy as _np
    _FLOAT_DTYPE = _np.dtype("float64")
    _INT_DTYPE = _np.dtype("int64")
except Exception:
    _FLOAT_DTYPE = None
    _INT_DTYPE = None


def _safe_float(val: Any = 0.0, default: float = 0.0) -> float:
    """
    Convierte de forma segura `val` a float de Python.
    Si `val` es None, '', 'sin_dato', 'N/D', o strings no numéricos,
    devuelve `default` convertido a float.
    """
    if val is None or val == "" or val == "sin_dato" or val == "N/D":
        return _builtins.float(default)
    try:
        return _builtins.float(val)
    except (TypeError, ValueError):
        pass
    try:
        s = str(val).strip().replace("%", "")
        if not s or s.lower() in ("none", "null", "nan", "sin_dato", "n/d", "—", "-"):
            return _builtins.float(default)
        return _builtins.float(s)
    except (TypeError, ValueError):
        return _builtins.float(default)


def _safe_int(val: Any = 0, default: int = 0) -> int:
    """
    Convierte de forma segura `val` a int de Python redondeando el resultado.
    Si `val` es None o no numérico, devuelve `default`.
    """
    return _builtins.int(round(_safe_float(val, default)))


class _SafeFloatMeta(type):
    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, _builtins.float)


class _SafeFloat(float, metaclass=_SafeFloatMeta):
    """
    Clase callable que actúa como reemplazo transparente de `float` en el módulo.
    - Soporta float(val) y float(val, default)
    - isinstance(x, float) sigue funcionando exactamente igual vía __instancecheck__
    - np.asarray(..., dtype=float) se interpreta como float64 nativo vía dtype
    - Permite subclases como _CeldaTendencia
    - Nunca lanza TypeError si val es None
    """
    if _FLOAT_DTYPE is not None:
        dtype = _FLOAT_DTYPE

    def __new__(cls, val: Any = 0.0, default: float = 0.0) -> float:
        res = _safe_float(val, default)
        return _builtins.float.__new__(cls, res)


class _SafeIntMeta(type):
    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, _builtins.int)


class _SafeInt(int, metaclass=_SafeIntMeta):
    """
    Clase callable que actúa como reemplazo transparente de `int` en el módulo.
    - Soporta int(val) y int(val, default)
    - isinstance(x, int) sigue funcionando exactamente igual vía __instancecheck__
    - np.asarray(..., dtype=int) se interpreta como int64 nativo vía dtype
    - Permite subclases como _CeldaTendencia
    - Nunca lanza TypeError si val es None
    """
    if _INT_DTYPE is not None:
        dtype = _INT_DTYPE

    def __new__(cls, val: Any = 0, default: int = 0) -> int:
        res = _safe_int(val, default)
        return _builtins.int.__new__(cls, res)


def obtener_hash_corto_git(repo_dir: str | None = None) -> str:
    """Devuelve el hash corto de git del repositorio sin imprimir secretos."""
    import os
    import subprocess
    from pathlib import Path

    base = Path(repo_dir) if repo_dir else Path(__file__).resolve().parent
    git_dir = base / ".git"
    try:
        head_file = git_dir / "HEAD"
        if head_file.is_file():
            head_txt = head_file.read_text(encoding="utf-8", errors="ignore").strip()
            if head_txt.startswith("ref: "):
                ref_rel = head_txt[5:].strip()
                ref_file = git_dir / ref_rel
                if ref_file.is_file():
                    sha = ref_file.read_text(encoding="utf-8", errors="ignore").strip()
                    if len(sha) >= 7:
                        return sha[:7]
                packed_file = git_dir / "packed-refs"
                if packed_file.is_file():
                    for line in packed_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith("^"):
                            continue
                        parts = line.split(" ", 1)
                        if len(parts) == 2 and parts[1].strip() == ref_rel and len(parts[0]) >= 7:
                            return parts[0][:7]
            elif len(head_txt) >= 7:
                return head_txt[:7]
    except Exception:
        pass

    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(base),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
        if out:
            return out[:7]
    except Exception:
        pass

    gh_sha = (os.getenv("GITHUB_SHA") or "").strip()
    if len(gh_sha) >= 7:
        return gh_sha[:7]
    return "sin_hash"


def obtener_sello_build(
    repo_dir: str | None = None,
    ahora: Any = None,
) -> str:
    """Genera el sello de versión obligatorio 'build <hash corto> · YYYY-MM-DD HH:MM'."""
    from datetime import datetime

    hash_corto = obtener_hash_corto_git(repo_dir=repo_dir)
    if ahora is not None and hasattr(ahora, "strftime"):
        fecha_str = ahora.strftime("%Y-%m-%d %H:%M")
    elif isinstance(ahora, str) and ahora.strip():
        fecha_str = ahora.strip()
    else:
        fecha_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"build {hash_corto} · {fecha_str}"

