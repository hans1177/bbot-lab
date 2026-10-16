#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 파일명: assist/strategy_registry.py
# Config-driven strategy registry. No order/exchange operations.

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Callable

import strategy_runtime

BASE = Path(os.getenv('BOT_BASE_DIR', '/home/ec2-user/bot'))
STRATEGY_DIR = BASE / 'assist' / 'strategies'
_module_cache: dict[str, tuple[int, ModuleType]] = {}


def _normalize(name: str) -> str:
    return str(name or '').strip().upper()


def _default_material_key(name: str) -> str:
    return _normalize(name).lower()


def registry_config() -> dict:
    return strategy_runtime.registry()


def info(name: str) -> dict:
    name = _normalize(name)
    cfg = registry_config()
    row = cfg.get(name)
    out = dict(row) if isinstance(row, dict) else {}
    out['name'] = name
    out.setdefault('material_key', _default_material_key(name))
    return out


def names() -> list[str]:
    cfg = registry_config()
    out = []
    if isinstance(cfg, dict):
        for name in cfg:
            name = _normalize(name)
            if name:
                out.append(name)
    return out


def has_strategy(name: str) -> bool:
    return _normalize(name) in set(names())


def material_key(name: str) -> str:
    return str(info(name).get('material_key') or '').strip().lower()


def module_filename(name: str) -> str:
    row = info(name)
    raw = str(row.get('file') or '').strip()
    if raw:
        return raw
    module = str(row.get('module') or '').strip()
    if module:
        return module.rsplit('.', 1)[-1] + '.py'
    return ''


def module_path(name: str):
    filename = module_filename(name)
    if not filename:
        return None
    return STRATEGY_DIR / filename


def _load_module(name: str):
    name = _normalize(name)
    path = module_path(name)
    if path is None or not path.is_file():
        return None
    try:
        mt = path.stat().st_mtime_ns
    except Exception:
        return None
    cached = _module_cache.get(name)
    if cached and cached[0] == mt:
        return cached[1]
    spec = importlib.util.spec_from_file_location(f'assist_dynamic_{name.lower()}', str(path))
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _module_cache[name] = (mt, mod)
    return mod


def signal_callable(name: str):
    mod = _load_module(name)
    if mod is None:
        return None
    fn = getattr(mod, 'signal', None)
    return fn if callable(fn) else None


def strategy_signal(name: str, df):
    name = _normalize(name)
    fn = signal_callable(name)
    if fn is None:
        return {
            'signal': 'wait',
            'strength_long': 0.0,
            'strength_short': 0.0,
            'reason': f'NO_STRATEGY_MODULE:{name}',
        }
    params = strategy_runtime.strategy_params(name)
    try:
        return fn(df, params)
    except TypeError:
        return fn(df)


def enabled_strategy_names() -> list[str]:
    out = []
    seen = set()
    for tf in strategy_runtime.enabled_frames():
        for name in strategy_runtime.frame_strategies(tf):
            if name in seen:
                continue
            seen.add(name)
            out.append(name)
    return out


def enabled_callables() -> dict[str, Callable]:
    out = {}
    for name in enabled_strategy_names():
        fn = signal_callable(name)
        if fn is not None:
            out[name] = fn
    return out


def validate_modules() -> dict:
    errors = []
    for name in enabled_strategy_names():
        row = info(name)
        engine = str(row.get('engine') or 'ASSIST_FILE').strip().upper()
        if engine == 'BUILTIN_MATERIAL':
            continue
        p = module_path(name)
        if p is None or not p.is_file():
            errors.append(f'{name}: strategy file missing')
            continue
        if signal_callable(name) is None:
            errors.append(f'{name}: signal() missing')
    return {'ok': not errors, 'errors': errors, 'enabled': enabled_strategy_names()}
