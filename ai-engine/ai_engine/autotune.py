from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Deque, Dict, Any, Optional, Tuple
import json
import os
import time
import statistics as stats


# -----------------------------
# Profiles (Comfort-first / Eco-first)
# -----------------------------
PROFILES: Dict[str, Dict[str, Any]] = {
    "comfort": {
        "tuning_window_min": 30,
        "tuning_period_min": 10,
        "kp_min": 0.90,
        "kp_max": 1.30,
        "kp_step": 0.05,
        "base_min": 45.0,
        "base_max": 55.0,
        "base_step": 1.0,

        # thresholds
        "slow_avg_error_c": 0.40,
        "slow_trend_abs_cpm": 0.05,
        "osc_amp_room_c": 0.30,
        "overshoot_c": 0.50,
        "trend_noise_cpm": 0.06,
        "min_samples": 12,
    },
    "eco": {
        "tuning_window_min": 60,
        "tuning_period_min": 15,
        "kp_min": 0.60,
        "kp_max": 1.00,
        "kp_step": 0.03,
        "base_min": 42.0,
        "base_max": 50.0,
        "base_step": 0.5,

        "slow_avg_error_c": 0.45,
        "slow_trend_abs_cpm": 0.04,
        "osc_amp_room_c": 0.22,
        "overshoot_c": 0.40,
        "trend_noise_cpm": 0.05,
        "min_samples": 18,
    },
}


@dataclass
class ControlParams:
    kp: float
    base: float
    profile: str = "comfort"


@dataclass
class MetricSample:
    ts: float
    room_temp_c: float
    room_target_c: float
    error_c: float
    trend_c_per_min: float
    ch_setpoint_c: Optional[float] = None
    hold_active: Optional[bool] = None


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class AutoTuner:
    """
    Auto-tuning di Kp e BASE basato su finestra mobile di metrics.
    - Aggiorna i params al massimo 1 volta per ciclo (Kp OR BASE).
    - Ritorna un evento (da pubblicare su ai/tuning) con reason e KPI.
    """

    def __init__(
        self,
        initial: ControlParams,
        persist_path: str = "./data/autotune_state.json",
    ) -> None:
        self.params = initial
        self.persist_path = persist_path
        self.samples: Deque[MetricSample] = deque()
        self.last_tune_ts: float = 0.0
        self._load_state_if_any()

    def _profile_cfg(self) -> Dict[str, Any]:
        if self.params.profile not in PROFILES:
            self.params.profile = "comfort"
        return PROFILES[self.params.profile]

    def _load_state_if_any(self) -> None:
        if not os.path.exists(self.persist_path):
            return
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            self.params.kp = float(obj.get("kp", self.params.kp))
            self.params.base = float(obj.get("base", self.params.base))
            self.params.profile = str(obj.get("profile", self.params.profile))
            self.last_tune_ts = float(obj.get("last_tune_ts", self.last_tune_ts))
        except Exception:
            # non bloccare il motore se file corrotto
            pass

    def _persist_state(self) -> None:
        dirpath = os.path.dirname(self.persist_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)

        payload = {
            "kp": self.params.kp,
            "base": self.params.base,
            "profile": self.params.profile,
            "last_tune_ts": self.last_tune_ts,
        }
        tmp = self.persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.persist_path)

    def ingest_metrics(self, metrics: Dict[str, Any]) -> None:
        """
        metrics attesi (minimo):
          - room_temp_c
          - room_target_c
          - error_c
          - trend_c_per_min
        opzionali:
          - ch_setpoint_c
          - hold_active (bool)
          - ts (epoch numerico). Se mancante/non numerico, usa time.time()
        """
        ts = metrics.get("ts")
        if not isinstance(ts, (int, float)):
            ts = time.time()

        s = MetricSample(
            ts=float(ts),
            room_temp_c=float(metrics["room_temp_c"]),
            room_target_c=float(metrics["room_target_c"]),
            error_c=float(metrics["error_c"]),
            trend_c_per_min=float(metrics["trend_c_per_min"]),
            ch_setpoint_c=float(metrics["ch_setpoint_c"])
            if ("ch_setpoint_c" in metrics and metrics["ch_setpoint_c"] is not None)
            else None,
            hold_active=bool(metrics["hold_active"])
            if ("hold_active" in metrics and metrics["hold_active"] is not None)
            else None,
        )
        self.samples.append(s)
        self._prune_old_samples()

    def _prune_old_samples(self) -> None:
        cfg = self._profile_cfg()
        window_sec = cfg["tuning_window_min"] * 60

        # ✅ riferimento coerente col ts dei campioni
        now_ts = self.samples[-1].ts if self.samples else time.time()

        while self.samples and (now_ts - self.samples[0].ts) > window_sec:
            self.samples.popleft()

    def _compute_kpis(self) -> Dict[str, Any]:
        cfg = self._profile_cfg()
        if len(self.samples) < cfg["min_samples"]:
            return {"ready": False, "n": len(self.samples)}

        errors = [s.error_c for s in self.samples]
        temps = [s.room_temp_c for s in self.samples]
        targets = [s.room_target_c for s in self.samples]
        trends = [s.trend_c_per_min for s in self.samples]

        avg_error = stats.fmean(errors)
        avg_target = stats.fmean(targets)
        overshoot = max(0.0, max(temps) - avg_target)

        osc_amp = 0.5 * (max(temps) - min(temps))
        trend_std = stats.pstdev(trends) if len(trends) >= 2 else 0.0

        hold_vals = [s.hold_active for s in self.samples if s.hold_active is not None]
        hold_ratio = (sum(1 for v in hold_vals if v) / len(hold_vals)) if hold_vals else None

        return {
            "ready": True,
            "n": len(self.samples),
            "avg_error_c": avg_error,
            "overshoot_c": overshoot,
            "osc_amp_room_c": osc_amp,
            "trend_std_cpm": trend_std,
            "avg_target_c": avg_target,
            "hold_ratio": hold_ratio,
        }

    def maybe_tune(self) -> Optional[Tuple[ControlParams, Dict[str, Any]]]:
        """
        Se è tempo e ci sono dati sufficienti -> aggiorna Kp o BASE (uno solo).
        Ritorna (params, tuning_event) oppure None se non ha tunato.
        """
        cfg = self._profile_cfg()
        now = time.time()
        period_sec = cfg["tuning_period_min"] * 60

        if (now - self.last_tune_ts) < period_sec:
            return None

        kpi = self._compute_kpis()
        if not kpi.get("ready"):
            return None

        # ✅ se stai quasi sempre in hold, il tuning è rumore
        hold_ratio = kpi.get("hold_ratio")
        if isinstance(hold_ratio, (int, float)) and hold_ratio >= 0.50:
            self.last_tune_ts = now
            self._persist_state()
            return None

        avg_error = float(kpi["avg_error_c"])
        overshoot = float(kpi["overshoot_c"])
        osc_amp = float(kpi["osc_amp_room_c"])
        trend_std = float(kpi["trend_std_cpm"])
        trends = [s.trend_c_per_min for s in self.samples]
        avg_trend_abs = abs(stats.fmean(trends))

        slow_response = (avg_error > cfg["slow_avg_error_c"]) and (avg_trend_abs < cfg["slow_trend_abs_cpm"])

        oscillating = (
            (osc_amp > cfg["osc_amp_room_c"])
            or (overshoot > cfg["overshoot_c"])
            or (trend_std > cfg["trend_noise_cpm"])
        )

        # ✅ caso diverso da slow_response:
        # sei sotto target ma non "piatto" -> spesso conviene alzare BASE prima (curva climatica)
        underpowered = (avg_error > cfg["slow_avg_error_c"]) and (avg_trend_abs >= cfg["slow_trend_abs_cpm"]) and (not oscillating)

        too_hot = (overshoot > cfg["overshoot_c"]) and (not oscillating)

        changed = False
        reason = None
        old = {"kp": self.params.kp, "base": self.params.base}

        # Priorità: stabilità > aggressività
        if oscillating:
            new_kp = clamp(self.params.kp - cfg["kp_step"], cfg["kp_min"], cfg["kp_max"])
            if new_kp != self.params.kp:
                self.params.kp = new_kp
                changed = True
                reason = "oscillating_or_overshoot_reduce_kp"
            else:
                new_base = clamp(self.params.base - cfg["base_step"], cfg["base_min"], cfg["base_max"])
                if new_base != self.params.base:
                    self.params.base = new_base
                    changed = True
                    reason = "oscillating_reduce_base"

        elif slow_response:
            new_kp = clamp(self.params.kp + cfg["kp_step"], cfg["kp_min"], cfg["kp_max"])
            if new_kp != self.params.kp:
                self.params.kp = new_kp
                changed = True
                reason = "slow_response_increase_kp"
            else:
                new_base = clamp(self.params.base + cfg["base_step"], cfg["base_min"], cfg["base_max"])
                if new_base != self.params.base:
                    self.params.base = new_base
                    changed = True
                    reason = "slow_response_increase_base"

        else:
            if underpowered:
                new_base = clamp(self.params.base + cfg["base_step"], cfg["base_min"], cfg["base_max"])
                if new_base != self.params.base:
                    self.params.base = new_base
                    changed = True
                    reason = "underpowered_raise_base"
            elif too_hot:
                new_base = clamp(self.params.base - cfg["base_step"], cfg["base_min"], cfg["base_max"])
                if new_base != self.params.base:
                    self.params.base = new_base
                    changed = True
                    reason = "overshoot_lower_base"

        # se non cambia nulla, aggiorna timestamp per non ricalcolare troppo spesso
        if not changed:
            self.last_tune_ts = now
            self._persist_state()
            return None

        self.last_tune_ts = now
        self._persist_state()

        event = {
            "ts": now,
            "profile": self.params.profile,
            "old": old,
            "new": {"kp": self.params.kp, "base": self.params.base},
            "reason": reason,
            "kpi": kpi,
        }
        return self.params, event
