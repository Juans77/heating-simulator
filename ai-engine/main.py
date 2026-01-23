import json, os, time, logging
import paho.mqtt.client as mqtt

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

TELEMETRY_TOPIC = os.getenv("TELEMETRY_TOPIC", "otgw/telemetry")
CMD_TOPIC = os.getenv("CMD_TOPIC", "otgw/cmd")

AI_MODE_TOPIC = os.getenv("AI_MODE_TOPIC", "ai/mode")
ROOM_TARGET_TOPIC = os.getenv("ROOM_TARGET_TOPIC", "ai/room_target_c")
DECISION_TOPIC = os.getenv("DECISION_TOPIC", "ai/decision")

MIN_SETPOINT = 30.0
MAX_SETPOINT = 80.0
PUBLISH_MIN_INTERVAL_S = 20

ai_mode = "OFF"
room_target = 20.0

last_publish_ts = 0.0

Kp = 8.0
base = 35.0

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def compute_setpoint(telemetry):
    room = float(telemetry.get("room_temp_c", 20.0))
    outside = float(telemetry.get("outside_temp_c", 10.0))
    err = room_target - room
    outside_comp = clamp((10.0 - outside) * 0.5, -5.0, 8.0)
    sp = base + (Kp * err) + outside_comp
    flame = bool(telemetry.get("flame_on", False))
    if flame and err < 0.3:
        sp -= 3.0
    return clamp(sp, MIN_SETPOINT, MAX_SETPOINT)

def should_disable_ch(telemetry):
    """Determine if CH should be disabled (room too hot)"""
    room = float(telemetry.get("room_temp_c", 20.0))
    ch_enabled = bool(telemetry.get("ch_enabled", True))
    
    # Hysteresis: disable if too hot, enable if too cold
    if room > room_target + 1.0:
        return True  # Disable CH
    elif room < room_target - 0.5:
        return False  # Enable CH
    else:
        return not ch_enabled  # Keep current state

def publish_cmd(client, setpoint_c, ch_enabled=True):
    global last_publish_ts
    payload = json.dumps({
        "ch_setpoint_c": round(setpoint_c, 1),
        "ch_enabled": ch_enabled
    })
    client.publish(CMD_TOPIC, payload, qos=0, retain=False)
    last_publish_ts = time.time()
    
    logger.info(f"Published setpoint: {round(setpoint_c, 1)}°C, CH enabled: {ch_enabled}")
    
    client.publish(DECISION_TOPIC, json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ai_mode": ai_mode,
        "room_target_c": room_target,
        "chosen_setpoint_c": round(setpoint_c, 1),
        "ch_enabled": ch_enabled,
    }), qos=0, retain=False)

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Connected to MQTT broker successfully")
    else:
        logger.error(f"Connection failed with code {rc}")
        return
    
    client.subscribe([(TELEMETRY_TOPIC, 0), (AI_MODE_TOPIC, 0), (ROOM_TARGET_TOPIC, 0)])
    logger.info(f"Subscribed to topics: {TELEMETRY_TOPIC}, {AI_MODE_TOPIC}, {ROOM_TARGET_TOPIC}")
    
    client.publish(DECISION_TOPIC, json.dumps({"status": "online"}), qos=0, retain=False)

def on_message(client, userdata, msg):
    global ai_mode, room_target
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()

    if topic == AI_MODE_TOPIC:
        old_mode = ai_mode
        ai_mode = payload.upper()
        if old_mode != ai_mode:
            logger.info(f"AI Mode changed: {old_mode} → {ai_mode}")
        return
    
    if topic == ROOM_TARGET_TOPIC:
        try:
            new_target = float(payload)
            if new_target != room_target:
                logger.info(f"Room target changed: {room_target}°C → {new_target}°C")
                room_target = new_target
        except Exception as e:
            logger.warning(f"Invalid room target value: {payload} - {e}")
        return
    
    if topic == TELEMETRY_TOPIC:
        if ai_mode != "ON":
            return
        
        now = time.time()
        if now - last_publish_ts < PUBLISH_MIN_INTERVAL_S:
            return
        
        try:
            telemetry = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid telemetry JSON: {e}")
            return
        
        sp = compute_setpoint(telemetry)
        ch_should_be_disabled = should_disable_ch(telemetry)
        publish_cmd(client, sp, ch_enabled=not ch_should_be_disabled)

def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    
    # Retry loop for connection
    while True:
        try:
            logger.info(f"Connecting to MQTT broker at {MQTT_HOST}:{MQTT_PORT}...")
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            logger.error(f"Connection failed: {e}. Retrying in 5s...")
            time.sleep(5)
    
    logger.info("Connected! Starting loop...")
    client.loop_forever()

if __name__ == "__main__":
    main()