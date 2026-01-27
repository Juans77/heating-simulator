import json
import os
import time
import logging

import paho.mqtt.client as mqtt

from collections import deque

from ai_engine.autotune import AutoTuner, ControlParams  # ✅ import OK

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

TELEMETRY_TOPIC = os.getenv("TELEMETRY_TOPIC", "otgw/telemetry")
CMD_TOPIC = os.getenv("CMD_TOPIC", "otgw/cmd")

AI_MODE_TOPIC = os.getenv("AI_MODE_TOPIC", "ai/mode")
ROOM_TARGET_TOPIC = os.getenv("ROOM_TARGET_TOPIC", "ai/room_target_c")

DECISION_TOPIC = os.getenv("DECISION_TOPIC", "ai/decision")
STATUS_TOPIC = os.getenv("STATUS_TOPIC", "ai/engine/status")
METRICS_TOPIC = os.getenv("METRICS_TOPIC", "ai/metrics")
ALERTS_TOPIC = os.getenv("ALERTS_TOPIC", "ai/alerts")
TUNING_TOPIC = os.getenv("TUNING_TOPIC", "ai/tuning")

MIN_SETPOINT = float(os.getenv("MIN_SETPOINT", "30"))
MAX_SETPOINT = float(os.getenv("MAX_SETPOINT", "80"))

DEADBAND_C = float(os.getenv("DEADBAND_C", "0.2"))
MAX_STEP_C = float(os.getenv("MAX_STEP_C", "1.0"))
PUBLISH_MIN_INTERVAL_S = int(os.getenv("PUBLISH_MIN_INTERVAL_S", "60"))
PUBLISH_MIN_DELTA_C = float(os.getenv("PUBLISH_MIN_DELTA_C", "0.5"))

# -----------------------------
# Step 4: Auto-tuning (UNICA init)
# -----------------------------
# ✅ usa PROFILE (compatibile con AI_PROFILE)
INITIAL_PROFILE = os.getenv("PROFILE", os.getenv("AI_PROFILE", "comfort")).strip().lower()  # comfort | eco


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


tuner = AutoTuner(
    initial=ControlParams(
        kp=float(os.getenv("KP", "8.0")),
        base=float(os.getenv("BASE", "35.0")),
        profile=INITIAL_PROFILE if INITIAL_PROFILE in ("comfort", "eco") else "comfort",
    ),
    persist_path=os.getenv("AUTOTUNE_STATE", "/data/autotune_state.json"),
)

# clamp iniziale ai limiti profilo
cfg = tuner._profile_cfg()
tuner.params.kp = clamp(tuner.params.kp, cfg["kp_min"], cfg["kp_max"])
tuner.params.base = clamp(tuner.params.base, cfg["base_min"], cfg["base_max"])

# alias locali usati dal controller (GLOBAL aggiornabili da autotune)
Kp = tuner.params.kp
BASE = tuner.params.base

# -----------------------------
# Step 2: finestra/dispersone
# -----------------------------
DROP_THRESHOLD_C_PER_MIN = float(os.getenv("DROP_THRESHOLD_C_PER_MIN", "0.35"))
WINDOW_HOLD_MIN = int(os.getenv("WINDOW_HOLD_MIN", "10"))
ALERT_COOLDOWN_S = int(os.getenv("ALERT_COOLDOWN_S", "600"))

# -----------------------------
# Step 3: diagnosi inefficienza riscaldamento
# -----------------------------
INEFF_ERR_MIN_C = float(os.getenv("INEFF_ERR_MIN_C", "0.8"))
INEFF_MOD_MIN = float(os.getenv("INEFF_MOD_MIN", "60"))
INEFF_TREND_MAX_C_PER_MIN = float(os.getenv("INEFF_TREND_MAX_C_PER_MIN", "0.03"))
INEFF_DURATION_S = int(os.getenv("INEFF_DURATION_S", "300"))
INEFF_ALERT_COOLDOWN_S = int(os.getenv("INEFF_ALERT_COOLDOWN_S", "900"))
INEFF_TEST_BOOST_C = float(os.getenv("INEFF_TEST_BOOST_C", "5.0"))
INEFF_TEST_MIN = int(os.getenv("INEFF_TEST_MIN", "10"))

ai_mode = "OFF"
room_target = 20.0

last_publish_ts = 0.0
last_sent_setpoint = None

last_room_temp = None
last_room_ts = None

# storico temperatura per trend stabile (evita falsi picchi su dt piccoli)
room_hist = deque(maxlen=180)  # ~180 campioni -> ok anche se telemetria ~2s


hold_until_ts = 0.0
last_alert_ts = 0.0

ineff_start_ts = 0.0
ineff_boost_until_ts = 0.0
last_ineff_alert_ts = 0.0


def publish_json(client, topic, obj, retain=False):
    client.publish(topic, json.dumps(obj), qos=0, retain=retain)


def publish_status(client, status: str):
    publish_json(client, STATUS_TOPIC, {"status": status}, retain=True)


def publish_metrics(client, metrics: dict):
    publish_json(client, METRICS_TOPIC, metrics, retain=False)


def publish_decision(client, decision: dict):
    publish_json(client, DECISION_TOPIC, decision, retain=False)


def publish_alert(client, alert: dict):
    publish_json(client, ALERTS_TOPIC, alert, retain=False)


def publish_tuning(client, tuning_event: dict):
    # ✅ tuning = evento, NON stato -> no retain
    publish_json(client, TUNING_TOPIC, tuning_event, retain=False)


def compute_outside_comp(outside_c: float) -> float:
    return clamp((10.0 - outside_c) * 0.5, -5.0, 8.0)


def compute_setpoint_raw(room_c: float, outside_c: float, flame_on: bool) -> float:
    err_local = room_target - room_c
    sp = BASE + (Kp * err_local) + compute_outside_comp(outside_c)
    if flame_on and err_local < 0.3:
        sp -= 3.0
    return clamp(sp, MIN_SETPOINT, MAX_SETPOINT)


def apply_deadband(sp_raw: float, err: float, fallback_sp: float) -> float:
    if abs(err) <= DEADBAND_C:
        return fallback_sp
    return sp_raw


def apply_max_step(sp_desired: float, reference_sp: float) -> float:
    diff = sp_desired - reference_sp
    if diff > MAX_STEP_C:
        return reference_sp + MAX_STEP_C
    if diff < -MAX_STEP_C:
        return reference_sp - MAX_STEP_C
    return sp_desired


def compute_ch_enabled(room_c: float, current_ch_enabled: bool) -> bool:
    if room_c > room_target + 1.0:
        return False
    if room_c < room_target - 0.5:
        return True
    return current_ch_enabled


def publish_cmd(client, setpoint_c: float, ch_enabled: bool):
    global last_publish_ts, last_sent_setpoint
    payload = {"ch_setpoint_c": round(setpoint_c, 1), "ch_enabled": bool(ch_enabled)}
    client.publish(CMD_TOPIC, json.dumps(payload), qos=0, retain=False)
    last_publish_ts = time.time()
    last_sent_setpoint = float(setpoint_c)
    logger.info(f"CMD -> setpoint={round(setpoint_c, 1)}°C, ch_enabled={ch_enabled}")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT broker")
        publish_status(client, "online")
        client.subscribe([(TELEMETRY_TOPIC, 0), (AI_MODE_TOPIC, 0), (ROOM_TARGET_TOPIC, 0)])
        logger.info(f"Subscribed: {TELEMETRY_TOPIC}, {AI_MODE_TOPIC}, {ROOM_TARGET_TOPIC}")
    else:
        logger.error(f"MQTT connect failed rc={rc}")


def on_message(client, userdata, msg):
    global ai_mode, room_target
    global last_publish_ts, last_sent_setpoint, last_room_temp, last_room_ts
    global hold_until_ts, last_alert_ts
    global ineff_start_ts, ineff_boost_until_ts, last_ineff_alert_ts
    global Kp, BASE  # ✅ aggiornati da autotune

    topic = msg.topic
    payload_str = msg.payload.decode("utf-8", errors="ignore").strip()

    if topic == AI_MODE_TOPIC:
        old = ai_mode
        ai_mode = payload_str.upper()
        if old != ai_mode:
            logger.info(f"AI Mode: {old} -> {ai_mode}")
        return

    if topic == ROOM_TARGET_TOPIC:
        try:
            new_target = float(payload_str)
            if new_target != room_target:
                logger.info(f"Room target: {room_target} -> {new_target}")
                room_target = new_target
        except Exception as e:
            logger.warning(f"Invalid room target '{payload_str}': {e}")
        return

    if topic != TELEMETRY_TOPIC or ai_mode != "ON":
        return

    now = time.time()

    try:
        telemetry = json.loads(payload_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid telemetry JSON: {e}")
        return

    room_c = float(telemetry.get("room_temp_c", 20.0))
    outside_c = float(telemetry.get("outside_temp_c", 10.0))
    flame_on = bool(telemetry.get("flame_on", False))
    current_sp = float(telemetry.get("ch_setpoint_c", 45.0))
    current_ch_enabled = bool(telemetry.get("ch_enabled", True))
    mod = float(telemetry.get("boiler_modulation_percent", 0.0))

        # trend (°C/min) su finestra ~60s per evitare falsi picchi (telemetria frequente)
    room_hist.append((now, room_c))
    trend_c_per_min = 0.0

    if len(room_hist) >= 2:
        # scegli un punto di riferimento almeno 45s fa (se presente)
        t0, r0 = room_hist[0]
        for (t_candidate, r_candidate) in room_hist:
            if now - t_candidate >= 45:
                t0, r0 = t_candidate, r_candidate

        dt = now - t0
        if dt >= 30:  # sicurezza
            trend_c_per_min = (room_c - r0) / dt * 60.0

    # mantengo anche questi due per compatibilità/diagnostica (non più usati per trend)
    last_room_temp = room_c
    last_room_ts = now


    err = room_target - room_c
    ref_sp = last_sent_setpoint if last_sent_setpoint is not None else current_sp

    # -----------------------------
    # STEP 2: detect rapido calo (possibile finestra)
    # -----------------------------
    if (err > 0.2) and (trend_c_per_min < -abs(DROP_THRESHOLD_C_PER_MIN)):
        if (now - last_alert_ts) >= ALERT_COOLDOWN_S:
            last_alert_ts = now
            hold_until_ts = max(hold_until_ts, now + WINDOW_HOLD_MIN * 60)

            publish_alert(
                client,
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "type": "possible_window_open",
                    "room_temp_c": room_c,
                    "room_target_c": room_target,
                    "trend_c_per_min": trend_c_per_min,
                    "hold_minutes": WINDOW_HOLD_MIN,
                },
            )
            logger.warning(
                f"ALERT window/open suspected: trend={trend_c_per_min:.2f} °C/min -> hold {WINDOW_HOLD_MIN}min"
            )

    hold_active = (now < hold_until_ts)

    # se siamo molto sopra target, annulla eventuale hold "stuck"
    if err <= -1.0 and hold_until_ts > 0:
        hold_until_ts = 0.0
        hold_active = False

    # -----------------------------
    # STEP 4: ingest metrics PER autotune
    # -----------------------------
    tuner.ingest_metrics(
        {
            "ts": now,
            "room_temp_c": room_c,
            "outside_temp_c": outside_c,
            "room_target_c": room_target,
            "error_c": err,
            "trend_c_per_min": trend_c_per_min,
            "ch_setpoint_c": ref_sp,
            "hold_active": hold_active,
        }
    )

    tune_result = tuner.maybe_tune()
    if tune_result:
        params, tuning_event = tune_result

        # ✅ clamp anche dopo autotune
        cfg = tuner._profile_cfg()
        Kp = clamp(params.kp, cfg["kp_min"], cfg["kp_max"])
        BASE = clamp(params.base, cfg["base_min"], cfg["base_max"])

        publish_tuning(client, tuning_event)
        logger.info(f"TUNING -> Kp={Kp:.3f}, BASE={BASE:.1f}, reason={tuning_event.get('reason')}")

    # -----------------------------
    # Se siamo in hold: spegni CH e stop
    # -----------------------------
    if hold_active:
        if now - last_publish_ts >= PUBLISH_MIN_INTERVAL_S:
            publish_cmd(client, setpoint_c=ref_sp, ch_enabled=False)

            publish_decision(
                client,
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "embedded",
                    "ai_mode": ai_mode,
                    "mode": "hold_window",
                    "room_target_c": room_target,
                    "room_temp_c": room_c,
                    "outside_temp_c": outside_c,
                    "trend_c_per_min": trend_c_per_min,
                    "chosen_setpoint_c": ref_sp,
                    "ch_enabled": False,
                },
            )

            publish_metrics(
                client,
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "room_temp_c": room_c,
                    "outside_temp_c": outside_c,
                    "room_target_c": room_target,
                    "error_c": err,
                    "trend_c_per_min": trend_c_per_min,
                    "hold_until_ts": hold_until_ts,
                    "hold_active": True,
                    "kp": Kp,
                    "base": BASE,
                },
            )
        return

    # -----------------------------
    # STEP 3: diagnosi inefficienza (solo quando AI vuole scaldare)
    # -----------------------------
    need_heat = err >= INEFF_ERR_MIN_C
    boiler_working = flame_on or (mod >= INEFF_MOD_MIN)
    not_warming = trend_c_per_min <= INEFF_TREND_MAX_C_PER_MIN

    if need_heat and boiler_working and not_warming:
        if ineff_start_ts == 0.0:
            ineff_start_ts = now

        if (now - ineff_start_ts) >= INEFF_DURATION_S:
            if (now - last_ineff_alert_ts) >= INEFF_ALERT_COOLDOWN_S:
                last_ineff_alert_ts = now
                ineff_boost_until_ts = max(ineff_boost_until_ts, now + INEFF_TEST_MIN * 60)

                publish_alert(
                    client,
                    {
                        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "type": "heating_inefficiency_suspected",
                        "room_temp_c": room_c,
                        "room_target_c": room_target,
                        "error_c": err,
                        "trend_c_per_min": trend_c_per_min,
                        "boiler_modulation_percent": mod,
                        "flame_on": flame_on,
                        "test_boost_c": INEFF_TEST_BOOST_C,
                        "test_minutes": INEFF_TEST_MIN,
                    },
                )
                logger.warning(
                    f"ALERT inefficiency suspected (err={err:.2f}, trend={trend_c_per_min:.3f}°C/min, mod={mod:.0f}%) -> boost {INEFF_TEST_BOOST_C}°C for {INEFF_TEST_MIN}min"
                )
    else:
        ineff_start_ts = 0.0

    # rate limit publish normale
    if now - last_publish_ts < PUBLISH_MIN_INTERVAL_S:
        return

    sp_raw = compute_setpoint_raw(room_c, outside_c, flame_on)

    # STEP 3: boost test per diagnosi inefficienza
    if now < ineff_boost_until_ts:
        sp_raw = clamp(sp_raw + INEFF_TEST_BOOST_C, MIN_SETPOINT, MAX_SETPOINT)

    sp_db = apply_deadband(sp_raw, err, fallback_sp=ref_sp)
    sp_smooth = apply_max_step(sp_db, reference_sp=ref_sp)

    sp_final = round(sp_smooth * 2.0) / 2.0
    sp_final = clamp(sp_final, MIN_SETPOINT, MAX_SETPOINT)

    ch_enabled = compute_ch_enabled(room_c, current_ch_enabled)

    delta_sp = abs(sp_final - ref_sp)
    ch_changed = ch_enabled != current_ch_enabled

    # Se siamo sopra target e CH è (o diventa) OFF, non serve inseguire il setpoint
    if (err <= -0.5) and (not ch_enabled) and (not ch_changed):
        return

    if (delta_sp < PUBLISH_MIN_DELTA_C) and (not ch_changed):
        return

    publish_cmd(client, sp_final, ch_enabled=ch_enabled)

    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    publish_decision(
        client,
        {
            "ts": ts_iso,
            "source": "embedded",
            "ai_mode": ai_mode,
            "room_target_c": room_target,
            "room_temp_c": room_c,
            "outside_temp_c": outside_c,
            "error_c": err,
            "trend_c_per_min": trend_c_per_min,
            "boiler_modulation_percent": mod,
            "chosen_setpoint_c": sp_final,
            "ch_enabled": ch_enabled,
            "kp": Kp,
            "base": BASE,
        },
    )

    publish_metrics(
        client,
        {
            "ts": ts_iso,
            "room_temp_c": room_c,
            "outside_temp_c": outside_c,
            "room_target_c": room_target,
            "error_c": err,
            "trend_c_per_min": trend_c_per_min,
            "boiler_modulation_percent": mod,
            "sp_raw_c": sp_raw,
            "sp_ref_c": ref_sp,
            "sp_final_c": sp_final,
            "delta_sp_c": delta_sp,
            "deadband_c": DEADBAND_C,
            "max_step_c": MAX_STEP_C,
            "min_interval_s": PUBLISH_MIN_INTERVAL_S,
            "min_delta_c": PUBLISH_MIN_DELTA_C,
            "hold_active": False,
            "kp": Kp,
            "base": BASE,
        },
    )


def main():
    # ✅ client_id stabile + LWT offline
    client = mqtt.Client(client_id="techloop-ai-engine", clean_session=True)
    client.will_set(STATUS_TOPIC, json.dumps({"status": "offline"}), qos=0, retain=True)

    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            logger.info(f"Connecting to MQTT {MQTT_HOST}:{MQTT_PORT} ...")
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            logger.error(f"Connect failed: {e}. Retry in 5s...")
            time.sleep(5)

    logger.info("Loop started")
    client.loop_forever()


if __name__ == "__main__":
    main()
