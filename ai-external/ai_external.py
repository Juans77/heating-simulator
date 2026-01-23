import os, json, time, threading
from datetime import datetime, timezone

import requests
import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")

DATASET_TOPIC = os.getenv("DATASET_TOPIC", "ai/dataset/embedded")
STATUS_TOPIC = os.getenv("STATUS_TOPIC", "ai/external/status")
DECISION_TOPIC = os.getenv("DECISION_TOPIC", "ai/external/decision")

HEARTBEAT_SECONDS = int(os.getenv("HEARTBEAT_SECONDS", "10"))
MIN_SETPOINT = float(os.getenv("MIN_SETPOINT", "30"))
MAX_SETPOINT = float(os.getenv("MAX_SETPOINT", "80"))

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def clamp(v: float) -> float:
    return max(MIN_SETPOINT, min(MAX_SETPOINT, v))

def try_pull_model():
    # Pull via API (if not present, Ollama will download on first use anyway in many cases,
    # but this makes startup more deterministic)
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/pull",
            json={"model": OLLAMA_MODEL, "stream": False},
            timeout=600,
        )
        # /api/pull can stream; some versions may still return 200 with stream events.
        # We don't hard-fail here.
        print(f"[ollama] pull status={r.status_code}")
    except Exception as e:
        print(f"[ollama] pull skipped/error: {e}")

def ask_model(dataset: dict) -> dict:
    """
    Return dict with chosen_setpoint_c (float), confidence (0..1), reason (string).
    Uses structured outputs via /api/chat + JSON schema.
    """
    system = (
        "Sei un controllore di caldaia. Devi produrre SOLO un JSON valido."
        " Obiettivo: scegliere chosen_setpoint_c (°C) stabile e sicuro."
        f" Vincoli: {MIN_SETPOINT} <= chosen_setpoint_c <= {MAX_SETPOINT}."
        " Se i dati sono incoerenti, scegli un valore conservativo vicino all'otgw_setpoint."
    )

    # Keep prompt short & numeric to reduce latency.
    user = (
        "Dati (JSON):\n"
        f"{json.dumps(dataset, ensure_ascii=False)}\n\n"
        "Restituisci un JSON con campi: chosen_setpoint_c (number), confidence (number 0..1), reason (string)."
    )

    schema = {
        "type": "object",
        "properties": {
            "chosen_setpoint_c": {"type": "number"},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["chosen_setpoint_c", "confidence", "reason"],
        "additionalProperties": False,
    }

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": schema,
    }

    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()

    # docs: response.message.content contains the content string. :contentReference[oaicite:4]{index=4}
    content = data.get("message", {}).get("content", "")
    result = json.loads(content)

    # Safety clamp
    result["chosen_setpoint_c"] = float(clamp(float(result["chosen_setpoint_c"])))
    result["confidence"] = float(max(0.0, min(1.0, float(result["confidence"]))))

    return result

class AIAgent:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        self.last_dataset = None
        self.lock = threading.Lock()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        print(f"[mqtt] connected reason_code={reason_code}")
        client.subscribe(DATASET_TOPIC, qos=0)

    def publish_status_loop(self):
        while True:
            try:
                self.client.publish(STATUS_TOPIC, "online", qos=0, retain=False)
            except Exception as e:
                print(f"[mqtt] status publish error: {e}")
            time.sleep(HEARTBEAT_SECONDS)

    def on_message(self, client, userdata, msg):
        try:
            dataset = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:
            print(f"[mqtt] invalid dataset json: {e}")
            return

        # Basic sanity: if missing critical fields, ignore
        if "room" not in dataset or "target" not in dataset or "otgw_setpoint" not in dataset:
            print("[ai] dataset missing fields; ignoring")
            return

        try:
            result = ask_model(dataset)
            decision = {
                "ts": now_iso(),
                "source": "external",
                "chosen_setpoint_c": result["chosen_setpoint_c"],
                "confidence": result["confidence"],
                "reason": result["reason"],
            }
            client.publish(DECISION_TOPIC, json.dumps(decision), qos=0, retain=False)
            print(f"[ai] published decision: {decision}")
        except Exception as e:
            print(f"[ai] inference error: {e}")

    def run(self):
        # Wait a bit for ollama to be up, then (try) pull model
        time.sleep(2)
        #try_pull_model()

        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

        t = threading.Thread(target=self.publish_status_loop, daemon=True)
        t.start()

        self.client.loop_forever()

if __name__ == "__main__":
    AIAgent().run()
