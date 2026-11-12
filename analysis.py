"""Rule-based analysis of the three FSR signals (heel, ball, toe).

No trained model and no diagnosis: it only describes what the sensors recorded.
"""
from datetime import datetime

ADC_MAX = 4095
LOAD_THRESHOLD = 300        # summed ADC below this = foot not loading the insole
SATURATION = 4000
PERIOD_DAYS = {"day": 1, "week": 7, "month": 30}
PERIOD_LABEL = {"day": "last 24 hours", "week": "last 7 days", "month": "last 30 days"}

DISCLAIMER = (
    "These observations come from rule-based analysis of three foot-pressure sensors. "
    "They are not a medical diagnosis and not a direct measure of knee force. "
    "Talk to a qualified clinician about any pain or symptoms."
)


def _pct(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return float(s[min(len(s) - 1, int(q * (len(s) - 1)))])


def count_heel_strikes(times: list[datetime], heel: list[int]) -> int:
    """Counts rises of the heel signal with hysteresis; ignores strikes < 250 ms apart."""
    peak = _pct(heel, 0.95)
    if peak < LOAD_THRESHOLD:
        return 0
    high, low = 0.5 * peak, 0.25 * peak
    armed, steps, last = True, 0, None
    for t, h in zip(times, heel):
        if armed and h >= high:
            if last is None or (t - last).total_seconds() >= 0.25:
                steps += 1
                last = t
            armed = False
        elif not armed and h <= low:
            armed = True
    return steps


def _shares(rows) -> dict[str, float]:
    totals = [0, 0, 0]
    for _, h, b, t in rows:
        totals[0] += h
        totals[1] += b
        totals[2] += t
    s = sum(totals) or 1
    return {k: round(v / s * 100, 1) for k, v in zip(("heel", "ball", "toe"), totals)}


def analyse(
    rows: list[tuple[datetime, int, int, int]],
    *,
    device_id: str,
    period: str,
    truncated: bool,
    sessions: list[tuple[datetime, datetime]],
    daily_goal: int,
) -> dict:
    """rows: (timestamp, heel, ball, toe), oldest first."""
    if not rows:
        raise ValueError("No sensor readings in this period yet. Walk with your insole, then try again.")

    loaded = [r for r in rows if r[1] + r[2] + r[3] >= LOAD_THRESHOLD]
    active_minutes = len({r[0].replace(second=0, microsecond=0) for r in loaded})
    shares = _shares(loaded) if loaded else {"heel": 0.0, "ball": 0.0, "toe": 0.0}
    means = {
        k: round(sum(r[i] for r in rows) / len(rows))
        for i, k in ((1, "heel"), (2, "ball"), (3, "toe"))
    }
    steps = count_heel_strikes([r[0] for r in rows], [r[1] for r in rows])
    session_minutes = round(sum((e - s).total_seconds() for s, e in sessions) / 60, 1)

    obs: list[str] = []
    recs: list[str] = []
    label = PERIOD_LABEL[period]

    obs.append(
        f"{len(rows):,} sensor samples were recorded in the {label}, "
        f"with the foot loading the insole during about {active_minutes} minute(s)."
    )
    if not loaded:
        obs.append("The pressure signals stayed very low, so the insole was probably not being worn.")
        recs.append("Wear the insole and walk for a few minutes so the sensors can record your steps.")
    else:
        obs.append(
            f"Of the measured pressure signal, the heel carried {shares['heel']}%, "
            f"the ball of the foot {shares['ball']}% and the toe {shares['toe']}% on average."
        )
        if steps:
            obs.append(f"About {steps:,} heel strikes were detected (an estimate from the heel sensor).")
        if shares["heel"] >= 55:
            obs.append("Most of the signal was at the heel, so your weight tended to land and stay on the heel.")
            recs.append("Check that your footwear has good heel cushioning and fits well.")
        elif shares["ball"] >= 55:
            obs.append("Most of the signal was at the ball of the foot, a forefoot-heavy pattern.")
            recs.append("Notice whether you lean forward when walking, and choose footwear with forefoot support.")
        elif shares["toe"] >= 40:
            obs.append("The toe sensor carried an unusually large share of the signal.")
            recs.append("Check that the insole sits flat and the toe sensor is not pressed by a tight shoe.")
        else:
            obs.append("Pressure was spread across heel, ball and toe without one zone dominating.")

        if len(loaded) >= 200:
            half = len(loaded) // 2
            first, second = _shares(loaded[:half]), _shares(loaded[half:])
            shift = round(second["heel"] - first["heel"], 1)
            if abs(shift) >= 10:
                direction = "rose" if shift > 0 else "fell"
                obs.append(
                    f"The heel's share {direction} by {abs(shift)} percentage points from the first to the "
                    f"second half of the period."
                )

    # Sensor health checks
    for i, zone in ((1, "heel"), (2, "ball"), (3, "toe")):
        zone_vals = [r[i] for r in loaded]
        if loaded and _pct(zone_vals, 0.95) < 50:
            recs.append(f"The {zone} sensor stayed near zero while you walked. Check its wiring and placement.")
        if rows and sum(1 for r in rows if r[i] >= SATURATION) / len(rows) > 0.2:
            recs.append(
                f"The {zone} sensor often hit its maximum reading. A larger pull-down resistor may help."
            )

    days = PERIOD_DAYS[period]
    if sessions:
        obs.append(f"You completed {len(sessions)} walking session(s), {session_minutes} minutes in total.")
    goal_total = daily_goal * days
    if session_minutes < goal_total:
        recs.append(
            f"Your personal goal is {daily_goal} min/day ({goal_total} min for this period). "
            "Build up gradually at a comfortable pace."
        )
    recs.append("Stop and rest if walking causes pain, and talk to a doctor or physiotherapist about persistent discomfort.")

    return {
        "device_id": device_id,
        "period": period,
        "samples": len(rows),
        "truncated": truncated,
        "from": rows[0][0].isoformat(),
        "to": rows[-1][0].isoformat(),
        "active_minutes": active_minutes,
        "steps_estimate": steps,
        "zone_share_percent": shares,
        "mean_adc": means,
        "sessions": len(sessions),
        "session_minutes": session_minutes,
        "observations": obs,
        "recommendations": recs,
        "disclaimer": DISCLAIMER,
    }
