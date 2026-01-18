import paho.mqtt.client as mqtt
from xiaomi_lightbar import Lightbar
import argparse
import threading
import time
import pyrf24
import crc
from struct import unpack

description = """
    MQTT subscriber for Xiaomi Lightbar Home Assistant MQTT Light integration.
    This script subscribes to the MQTT topic and controls the Xiaomi Lightbar based on the received messages by using the xiaomi_lightbar library.
"""

parser = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)

parser.add_argument("--broker", type=str, default="homeassistant.local", help="MQTT Broker")
parser.add_argument("--port", type=int, default=1883, help="MQTT Port")
parser.add_argument("--username", type=str, default="", help="MQTT Username")
parser.add_argument("--password", type=str, default="", help="MQTT Password")
parser.add_argument("--topic", type=str, default="xiaomi/lightbar", help="MQTT Topic")
parser.add_argument("--ce_pin", type=int, default=25, help="CE Pin")
parser.add_argument("--csn_pin", type=int, default=0, help="CSN Pin")
parser.add_argument("--remote_id", type=lambda x: int(x, 16), default=0xABCDEF, help="Remote ID")

args = parser.parse_args()

CE_PIN = args.ce_pin
CSN_PIN = args.csn_pin
REMOTE_ID = args.remote_id
BROKER = args.broker
PORT = args.port
USERNAME = args.username
PASSWORD = args.password
TOPIC = args.topic

# CRC16 configuration for packet validation
crc16_config = crc.Configuration(
    width=16,
    polynomial=0x1021,
    init_value=0xFFFE,
    final_xor_value=0x0000,
    reverse_input=False,
    reverse_output=False,
)
crc16 = crc.Calculator(crc16_config)

# Preamble for packet matching
preamble = 0x533914DD1C493412  # 8 bytes

# Create Lightbar and MqttController instances
lightbar = Lightbar(ce_pin=CE_PIN, csn_pin=CSN_PIN, remote_id=REMOTE_ID)

def strip_bits(num: int, msb: int, lsb: int, bit_count=96):
    """Strip msb and lsb bits of an int"""
    mask = (1 << (bit_count - msb)) - 1
    return (num & mask) >> lsb


def decode_packet(raw: bytes):
    """Decode a received packet
    
    Based on scan_lightbar_remote.py logic:
    - First 24 bits (MSB) are preamble trailing bits
    - 9 bytes = 72 bits are the payload (includes some junk in LSB)
    """
    # Strip the preamble bits (24 MSB)
    raw_int = int.from_bytes(raw, "big")
    data = strip_bits(raw_int, 24, 0)
    
    # Decode the payload
    keys = ["id", "separator", "counter", "command", "crc"]
    values = unpack('>3s s s 2s 2s', data.to_bytes(9, 'big'))
    values = (int.from_bytes(x, "big") for x in values)
    packet = dict(zip(keys, values))
    return packet


def validate_packet_crc(packet: dict) -> bool:
    """Check the CRC of a packet"""
    x = preamble.to_bytes(8, 'big')
    x += packet["id"].to_bytes(3, 'big')
    x += packet["separator"].to_bytes(1, 'big')
    x += packet["counter"].to_bytes(1, 'big')
    x += packet["command"].to_bytes(2, 'big')
    return packet["crc"] == crc16.checksum(x)

class MqttController:
    # Configuration constants
    TX_COMPLETION_DELAY = 0.3  # Delay in seconds for TX operations to complete
    POLLING_INTERVAL = 0.05  # Radio polling interval in seconds (50ms)
    THREAD_JOIN_TIMEOUT = 2  # Timeout for thread join on shutdown (seconds)
    TEMP_RANGE = 370 - 153  # Temperature range in mireds
    BRIGHTNESS_RANGE = 255  # Brightness range (0-255)
    MAX_STEPS = 15  # Maximum adjustment steps for commands
    STEP_DIVISOR = 15  # Divisor to scale step increments (step * range / divisor)
    PREAMBLE_SHIFT_BITS = 24  # Bits to shift preamble for RX pipe address
    
    def __init__(self, broker, port, username, password, topic, lightbar):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username != "":
            self.client.username_pw_set(username, password)
        self.broker = broker
        self.port = port
        self.topic = topic + "/#"
        self.base_topic = topic
        self.lightbar = lightbar
        # Store the previous control state to avoid sending the same on_off command multiple times
        # we assume the default state to be ON
        self.previous_control_state = "ON"
        self.current_brightness = 128
        self.current_temperature = 261
        
        # Use the lightbar's radio for receiving as well
        # We'll configure it for RX when not transmitting
        self.rx_radio = self.lightbar.radio
        
        # Setup RX pipe once during initialization
        # Convert address to bytes to avoid deprecation warning
        rx_address = (preamble >> self.PREAMBLE_SHIFT_BITS).to_bytes(5, 'big')
        self.rx_radio.open_rx_pipe(1, rx_address)
        print(f"RX pipe configured with address: {rx_address.hex()}")
        
        # Thread control
        self.listener_thread = None
        self.listener_running = False
        self.state_lock = threading.Lock()  # Protect shared state
        self.radio_lock = threading.Lock()  # Protect radio access

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
    
    def configure_radio_for_rx(self):
        """Configure radio for receiving knob commands"""
        with self.radio_lock:
            self.rx_radio.listen = False  # Stop listening first
            # Configure for RX based on scan_lightbar_remote.py
            self.rx_radio.dynamic_payloads = False
            self.rx_radio.crc_length = pyrf24.RF24_CRC_DISABLED
            self.rx_radio.payload_size = 12
            self.rx_radio.address_width = 5
            self.rx_radio.set_auto_ack(False)
            # Note: RX pipe is opened once during __init__, no need to reopen
            self.rx_radio.listen = True
    
    def configure_radio_for_tx(self):
        """Configure radio for transmitting commands"""
        with self.radio_lock:
            self.rx_radio.listen = False
            # Restore TX configuration
            self.rx_radio.dynamic_payloads = False
            self.rx_radio.payload_size = 17
            # Match RX configuration: explicitly disable CRC
            # (The Lightbar transmitter doesn't use CRC either)
            self.rx_radio.crc_length = pyrf24.RF24_CRC_DISABLED
            
    def send_command(self, command_func):
        """Send a command while temporarily switching to TX mode"""
        self.configure_radio_for_tx()
        try:
            command_func()
        finally:
            # Give time for transmission to complete
            time.sleep(self.TX_COMPLETION_DELAY)
            self.configure_radio_for_rx()
    
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def publish_state(self):
        self.client.publish(f"{self.base_topic}/state", self.previous_control_state, qos=1, retain=True)
        self.client.publish(f"{self.base_topic}/brightness", self.current_brightness, qos=1, retain=True)
        self.client.publish(f"{self.base_topic}/temperature", self.current_temperature, qos=1, retain=True)

    def on_connect(self, client, userdata, flags, rc, properties):
        if rc == 0:
            print("Connected to MQTT Broker!")
            client.subscribe(self.topic)
            self.publish_state()
        else:
            print(f"Failed to connect, return code: {rc}")
            self.stop()

    def on_message(self, client, userdata, msg):
        print(f"{msg.topic} {msg.payload}")
        with self.state_lock:
            if msg.topic == self.topic.replace("#", "control"):
                if msg.payload == b"ON":
                    if self.previous_control_state != "ON":
                        self.send_command(lambda: self.lightbar.on_off())
                        self.previous_control_state = "ON"
                        self.publish_state()
                if msg.payload == b"OFF":
                    if self.previous_control_state != "OFF":
                        self.send_command(lambda: self.lightbar.on_off())
                        self.previous_control_state = "OFF"
                        self.publish_state()

            elif msg.topic == self.topic.replace("#", "brightness/set"):
                val = int(msg.payload)
                self.current_brightness = val
                scaled_val = round((val / 255) * 15)
                print(f"Brightness: {scaled_val}")
                self.send_command(lambda: self.lightbar.brightness(scaled_val))
                self.publish_state()
            elif msg.topic == self.topic.replace("#", "temperature/set"):
                val = int(msg.payload)
                self.current_temperature = val
                scaled_val = scale_value(val)
                print(f"temperature: {scaled_val}")
                self.send_command(lambda: self.lightbar.color_temp(scaled_val))
                self.publish_state()

    def process_knob_command(self, command: int):
        """Process a command received from the physical knob/remote"""
        print(f"Knob command received: {hex(command)}")
        
        with self.state_lock:
            # Extract command type and value
            cmd_type = (command >> 8) & 0xFF
            cmd_value = command & 0xFF
            
            print(f"  Command type: {hex(cmd_type)}, value: {hex(cmd_value)}")
            
            # On/Off toggle (0x0100)
            if cmd_type == 0x01:
                # Toggle state
                old_state = self.previous_control_state
                if self.previous_control_state == "ON":
                    self.previous_control_state = "OFF"
                else:
                    self.previous_control_state = "ON"
                print(f"  State toggled: {old_state} → {self.previous_control_state}")
                self.publish_state()
            
            # Cooler color temperature (0x0201 to 0x020F)
            # Cooler = more blue/cool = lower mireds (towards 153 = 6500K)
            elif cmd_type == 0x02:
                step = cmd_value
                if 1 <= step <= self.MAX_STEPS:
                    # Cooler means LOWER temperature value in mireds
                    temp_change = step * self.TEMP_RANGE // self.STEP_DIVISOR
                    self.current_temperature = int(max(153, self.current_temperature - temp_change))
                    print(f"Temperature cooler (step {step}): {self.current_temperature} mireds")
                    self.publish_state()
            
            # Warmer color temperature (0x03FF to 0x03F1)
            # Warmer = more yellow/warm = higher mireds (towards 370 = 2700K)
            elif cmd_type == 0x03:
                step = 256 - cmd_value  # 0xFF=1, 0xFE=2, ..., 0xF1=15
                if 1 <= step <= self.MAX_STEPS:
                    # Warmer means HIGHER temperature value in mireds
                    temp_change = step * self.TEMP_RANGE // self.STEP_DIVISOR
                    self.current_temperature = int(min(370, self.current_temperature + temp_change))
                    print(f"Temperature warmer (step {step}): {self.current_temperature} mireds")
                    self.publish_state()
            
            # Higher brightness (0x0401 to 0x040F)
            elif cmd_type == 0x04:
                step = cmd_value
                if 1 <= step <= self.MAX_STEPS:
                    # Higher brightness
                    brightness_change = step * self.BRIGHTNESS_RANGE // self.STEP_DIVISOR
                    old_brightness = self.current_brightness
                    self.current_brightness = int(min(self.BRIGHTNESS_RANGE, self.current_brightness + brightness_change))
                    print(f"  Brightness higher (step {step}): {old_brightness} → {self.current_brightness} (+{brightness_change})")
                    self.publish_state()
            
            # Lower brightness (0x05FF to 0x05F1)
            elif cmd_type == 0x05:
                step = 256 - cmd_value  # 0xFF=1, 0xFE=2, ..., 0xF1=15
                if 1 <= step <= self.MAX_STEPS:
                    # Lower brightness
                    brightness_change = step * self.BRIGHTNESS_RANGE // self.STEP_DIVISOR
                    old_brightness = self.current_brightness
                    self.current_brightness = int(max(0, self.current_brightness - brightness_change))
                    print(f"  Brightness lower (step {step}): {old_brightness} → {self.current_brightness} (-{brightness_change})")
                    self.publish_state()
            
            # Reset (0x0600)
            elif cmd_type == 0x06:
                # Reset to medium brightness and warm color
                self.current_brightness = 128
                self.current_temperature = 153  # Warm
                print("  Reset to defaults: brightness=128, temperature=153")
                self.publish_state()
            
            # Unknown command type
            else:
                print(f"  WARNING: Unknown command type {hex(cmd_type)}")

    def knob_listener(self):
        """Background thread to listen for knob commands"""
        print("Knob listener thread started")
        # Initial configuration for RX
        self.configure_radio_for_rx()
        
        while self.listener_running:
            try:
                received = None
                with self.radio_lock:
                    if self.rx_radio.listen:  # Only check if in RX mode
                        has_payload, pipe_number = self.rx_radio.available_pipe()
                        if has_payload:
                            received = self.rx_radio.read(self.rx_radio.payload_size)
                
                # Process outside the lock to avoid blocking TX operations
                if received is not None:
                    packet = decode_packet(received)
                    if validate_packet_crc(packet):
                        self.process_knob_command(packet["command"])
                    else:
                        print("Received packet with invalid CRC, ignoring")
                        
                time.sleep(self.POLLING_INTERVAL)
            except Exception as e:
                print(f"Error in knob listener: {e}")
                time.sleep(0.1)
        print("Knob listener thread stopped")

    def start(self):
        try:
            self.client.connect(self.broker, self.port, 60)
            self.client.loop_start()
            
            # Start knob listener thread
            self.listener_running = True
            self.listener_thread = threading.Thread(target=self.knob_listener, daemon=True)
            self.listener_thread.start()
            print("Knob listener initialized")
        except Exception as e:
            print(f"Failed to connect to MQTT broker: {e}")

    def stop(self):
        # Stop knob listener thread
        if self.listener_thread is not None:
            self.listener_running = False
            self.listener_thread.join(timeout=self.THREAD_JOIN_TIMEOUT)
        
        self.client.loop_stop()
        self.client.disconnect()

def scale_value(t):
    if 153 <= t <= 219:
        a1, b1 = 153, 219
        c1, d1 = 15, 7
        f_t = c1 + (d1 - c1) * ((t - a1) / (b1 - a1))
    elif 219 < t <= 370:
        a2, b2 = 219, 370
        c2, d2 = 7, 0
        f_t = c2 + (d2 - c2) * ((t - a2) / (b2 - a2))
    else:
        f_t = None
    return round(f_t) if f_t is not None else None

def main():
    try:
        with MqttController(BROKER, PORT, USERNAME, PASSWORD, TOPIC, lightbar) as controller:
            controller.start()
            while True:  # Keep the program running
                time.sleep(1)  # Sleep to reduce CPU usage
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting...")

if __name__ == "__main__":
    main()
