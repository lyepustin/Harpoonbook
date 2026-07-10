from __future__ import annotations

from datetime import datetime
from typing import Any


SPOT_METHOD_PRIORS: dict[str, dict[str, float]] = {
    "cabo_roig_calas": {"espera": 1.0, "acecho": 0.8, "agujero": 0.6, "cerca_rompientes": 0.1, "mar_adentro": 0.2},
    "punta_glea": {"espera": 0.7, "acecho": 0.6, "agujero": 0.3, "cerca_rompientes": 0.9, "mar_adentro": 0.7},
    "cabo_roig_espigon_puerto": {"espera": 0.6, "acecho": 0.4, "agujero": 0.2, "cerca_rompientes": 1.0, "mar_adentro": 0.8},
    "campoamor_espigon_puerto": {"espera": 0.5, "acecho": 0.3, "agujero": 0.2, "cerca_rompientes": 1.0, "mar_adentro": 0.9},
}


def recommend(features: dict[str, Any], spot_id: str, top_n: int = 2) -> list[dict[str, Any]]:
    """
    Recomendación heurística de (spot, metodo) con score esperado (0-10 aprox).
    """
    method_ids = ["espera", "acecho", "agujero", "cerca_rompientes", "mar_adentro"]

    solunar_day = features.get("solunar_activity_day")  # e.g. MEDIA/ALTA/BAJA
    solunar_best_hour = features.get("solunar_best_hour")
    solunar_best_windows = features.get("solunar_best_windows") or []
    tide_dir = features.get("tide_direction")  # rising/falling/None
    hours_to_next = features.get("hours_to_next_tide_event")
    now_iso = features.get("timestamp")
    now_dt = datetime.fromisoformat(now_iso) if isinstance(now_iso, str) else None

    out: list[dict[str, Any]] = []
    base = 4.0  # baseline conservador para convertir en “score 0..10”
    prior = SPOT_METHOD_PRIORS.get(spot_id, {})

    for method_id in method_ids:
        score = base + float(prior.get(method_id, 0.0))
        reasons: list[str] = []

        if isinstance(solunar_day, str):
            day_u = solunar_day.upper()
            if day_u == "ALTA":
                score += 0.7
                reasons.append("Actividad solunar del día alta")
            elif day_u == "BAJA":
                score -= 0.7
                reasons.append("Actividad solunar del día baja")

        if isinstance(tide_dir, str):
            if tide_dir == "rising":
                # En rompiente/cerca-rompientes suele funcionar mejor al subir.
                if method_id in ("cerca_rompientes", "mar_adentro"):
                    score += 0.6
                else:
                    score += 0.3
                reasons.append("Marea subiendo")
            elif tide_dir == "falling":
                if method_id in ("cerca_rompientes", "espera"):
                    score += 0.6
                else:
                    score += 0.2
                reasons.append("Marea bajando")

        if isinstance(hours_to_next, (int, float)):
            if hours_to_next <= 2.0:
                score += 0.4
                reasons.append("Próximo cambio de marea cerca")

        if solunar_best_windows:
            highlighted = [w for w in solunar_best_windows if w.get("highlighted")]
            if highlighted:
                score += 1.0
                reasons.append("Franja solunar destacada (verde)")
                if method_id in ("acecho", "espera"):
                    score += 0.4

        if isinstance(solunar_best_hour, str) and now_dt is not None:
            entry_dt = datetime.fromisoformat(solunar_best_hour)
            hours_to_entry = (entry_dt - now_dt).total_seconds() / 3600.0
            if 0 <= hours_to_entry <= 1.0:
                score += 0.8
                reasons.append("Hora de entrada al agua en menos de 1 h")
            elif 0 < hours_to_entry <= 2.0:
                score += 0.4
                reasons.append("Hora de entrada al agua en las próximas 2 h")

        # Clamp 0..10
        score = max(0.0, min(10.0, score))

        out.append({"spot_id": spot_id, "method_id": method_id, "score": round(score, 2), "reasons": reasons})

    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top_n]

