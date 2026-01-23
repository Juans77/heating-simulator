import json
import os
import random
import time
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "otgw")
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL", "2"))

# Thermal simulation parameters (configurable)
HOUSE_THERMAL_MASS = float(os.getenv("HOUSE_THERMAL_MASS", "0.008"))  # how fast room temp changes
HOUSE_INSULATION = float(os.getenv("HOUSE_INSULATION", "0.15"))  # heat loss to outside (lower=better)
RADIATOR_EFFICIENCY = float(os.getenv("RADIATOR_EFFICIENCY", "0.25"))  # heat transfer from boiler to room

TELEMETRY_TOPIC = f"{BASE_TOPIC}/telemetry"
COMMAND_TOPIC = f"{BASE_TOPIC}/cmd"
STATUS_TOPIC = f"{BASE_TOPIC}/status"

# Simple internal state (can be adjusted via cmd topic)
state = {
    "ch_enabled": True,
    "flame_on": False,
    "ch_setpoint_c": 55.0,
    "outside_temp_c": 9.0,
    "room_temp_c": 20.5,
    "boiler_water_temp_c": 40.0,
    "return_water_temp_c": 35.0,
    "boiler_modulation_percent": 0.0,
}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Simulator connected to MQTT broker")
    else:
        logger.error(f"Connection failed with code {rc}")
        return
    
    client.publish(STATUS_TOPIC, json.dumps({"status": "online"}), retain=True)
    client.subscribe(COMMAND_TOPIC)
    logger.info(f"Subscribed to {COMMAND_TOPIC}")


def on_message(client, userdata, msg):
    # Expect JSON commands like:
    # {"ch_setpoint_c": 60, "ch_enabled": true}
    try:
        payload = msg.payload.decode("utf-8")
        data = json.loads(payload)
        if isinstance(data, dict):
            if "ch_enabled" in data:
                old = state["ch_enabled"]
                state["ch_enabled"] = bool(data["ch_enabled"])
                if old != state["ch_enabled"]:
                    logger.info(f"CH enabled: {old} → {state['ch_enabled']}")
            
            if "ch_setpoint_c" in data:
                old = state["ch_setpoint_c"]
                state["ch_setpoint_c"] = float(data["ch_setpoint_c"])
                if abs(old - state["ch_setpoint_c"]) > 0.5:
                    logger.info(f"CH setpoint: {old}°C → {state['ch_setpoint_c']}°C")
            
            if "outside_temp_c" in data:
                state["outside_temp_c"] = float(data["outside_temp_c"])
            if "room_temp_c" in data:
                state["room_temp_c"] = float(data["room_temp_c"])
    except Exception as e:
        logger.warning(f"Invalid command: {e}")


def step_physics(dt_s: float):
    """Improved thermal dynamics with realistic heat transfer."""

    # Outside temp drifts slowly with noise
    state["outside_temp_c"] += random.uniform(-0.05, 0.05)
    state["outside_temp_c"] = clamp(state["outside_temp_c"], -5.0, 25.0)

    # --- BOILER CONTROL ---
    # Determine if flame should be on
    if not state["ch_enabled"]:
        state["flame_on"] = False
    else:
        # Hysteresis control
        if state["boiler_water_temp_c"] < state["ch_setpoint_c"] - 2.0:
            state["flame_on"] = True
        elif state["boiler_water_temp_c"] > state["ch_setpoint_c"] + 1.0:
            state["flame_on"] = False

    # Modulation depends on how far from setpoint
    if state["flame_on"]:
        error = state["ch_setpoint_c"] - state["boiler_water_temp_c"]
        state["boiler_modulation_percent"] = clamp(25.0 + error * 8.0 + random.uniform(-2, 2), 10.0, 100.0)
    else:
        state["boiler_modulation_percent"] = clamp(state["boiler_modulation_percent"] - 10.0, 0.0, 100.0)

    # --- BOILER WATER TEMP ---
    if state["flame_on"]:
        heating = (state["boiler_modulation_percent"] / 100.0) * 1.8  # degC per interval
    else:
        heating = -0.6

    # Cooling depends on outside temp
    cooling_bias = (20.0 - state["outside_temp_c"]) * 0.02

    state["boiler_water_temp_c"] += (heating - cooling_bias) * (dt_s / PUBLISH_INTERVAL)
    state["boiler_water_temp_c"] = clamp(state["boiler_water_temp_c"], 15.0, 85.0)

    # --- RETURN WATER TEMP ---
    # Return temp follows boiler temp with lag (heat extracted by radiators)
    state["return_water_temp_c"] += (state["boiler_water_temp_c"] - 5.0 - state["return_water_temp_c"]) * 0.25
    state["return_water_temp_c"] = clamp(state["return_water_temp_c"], 10.0, 75.0)

    # --- ROOM TEMP (REALISTIC PHYSICS) ---
    # Heat input from radiators (depends on boiler-room delta)
    if state["ch_enabled"] and state["boiler_water_temp_c"] > state["room_temp_c"]:
        temp_delta = state["boiler_water_temp_c"] - state["room_temp_c"]
        heat_input = temp_delta * RADIATOR_EFFICIENCY * (state["boiler_modulation_percent"] / 100.0)
    else:
        heat_input = 0.0

    # Heat loss to outside (proportional to room-outside delta)
    temp_diff_outside = state["room_temp_c"] - state["outside_temp_c"]
    heat_loss = temp_diff_outside * HOUSE_INSULATION

    # Net change in room temp
    net_heat = heat_input - heat_loss
    state["room_temp_c"] += net_heat * HOUSE_THERMAL_MASS * (dt_s / PUBLISH_INTERVAL)
    state["room_temp_c"] = clamp(state["room_temp_c"], 10.0, 30.0)


def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # Last Will so HA can see offline if container dies
    client.will_set(STATUS_TOPIC, json.dumps({"status": "offline"}), retain=True)

    logger.info(f"Starting OTGW simulator...")
    logger.info(f"Thermal parameters: mass={HOUSE_THERMAL_MASS}, insulation={HOUSE_INSULATION}, radiator_eff={RADIATOR_EFFICIENCY}")

    while True:
        try:
            logger.info(f"Connecting to {MQTT_HOST}:{MQTT_PORT}...")
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            logger.error(f"Connection failed: {e}. Retrying in 2s...")
            time.sleep(2)

    client.loop_start()

    last = time.time()
    log_counter = 0

    while True:
        now = time.time()
        dt = now - last
        last = now

        step_physics(dt)

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ch_enabled": "true" if state["ch_enabled"] else "false",
            "flame_on": "true" if state["flame_on"] else "false",
            "ch_setpoint_c": round(state["ch_setpoint_c"], 1),
            "outside_temp_c": round(state["outside_temp_c"], 1),
            "room_temp_c": round(state["room_temp_c"], 1),
            "boiler_water_temp_c": round(state["boiler_water_temp_c"], 1),
            "return_water_temp_c": round(state["return_water_temp_c"], 1),
            "boiler_modulation_percent": round(state["boiler_modulation_percent"], 0),
        }

        client.publish(TELEMETRY_TOPIC, json.dumps(payload), qos=0, retain=False)
        
        # Log state every 30 seconds
        log_counter += 1
        if log_counter >= int(30 / PUBLISH_INTERVAL):
            logger.info(f"State: Room={state['room_temp_c']:.1f}°C, Boiler={state['boiler_water_temp_c']:.1f}°C, "
                       f"Setpoint={state['ch_setpoint_c']:.1f}°C, Flame={state['flame_on']}, Mod={state['boiler_modulation_percent']:.0f}%")
            log_counter = 0
        
        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    main()