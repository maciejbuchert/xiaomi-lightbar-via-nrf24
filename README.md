# Xiaomi Lightbar via NRF24

Control a [Xiaomi My Computer Monitor Light Bar](https://www.mi.com/global/product/mi-computer-monitor-light-bar/) MJGJD01YL, using a cheap 2.4 GHz radio transceiver module (nRF24L01 or nRF24L01+).

> [!IMPORTANT]
> There are two variants of the light bar, both of them controlled by 2.4 GHz radio signals (see the label on the bar for the device number).
> - Device number MJGJD01YL. It uses a TLSR8368 radio receiver and a proprietary radio format. This is the one that can be used with this library.
> - Device number MJGJD02YL. It uses a ESP32 and BLE (and even wifi, during the pairing process). This library does not support this model.

This library enables control of the Xiaomi Lightbar from a Raspberry Pi or similar device running Linux with GPIOs and a SPI controller. It includes an MQTT service for Home Assistant integration.

## Features

- Control Xiaomi Lightbar MJGJD01YL via nRF24L01 module
- Full support for all lightbar operations:
  - On/Off toggle
  - Brightness control (16 levels)
  - Color temperature control (16 levels, 2700K-6500K)
  - Incremental adjustments
- MQTT service for Home Assistant integration
- Scripts for testing and remote ID scanning

## Requirements

- Xiaomi My Computer Monitor Light Bar, model MJGJD01YL (without BLE or app)
- Raspberry Pi, or any other device running Linux with GPIOs and a SPI controller, and Python 3
- nRF24L01(+) module
- Python 3.7+

## Hardware Setup

Connect the Raspberry Pi to the nRF24L01 as shown [here](https://www.laboratoriogluon.com/conectar-raspberry-pi-3-a-nrf24l01).

```
```

## Installation

### Dependencies

This library requires the following Python packages:
- `pyrf24` - [pyRF24 python library](https://nrf24.github.io/pyRF24)
- `crc` - [CRC python library](https://github.com/Nicoretti/crc)
- `paho-mqtt` - MQTT client (for MQTT service)

Notice that `pyrf24` may need to build from source on some systems. In such case, you will need cmake and python headers (python3-dev) installed.

#### Debian based OS (e.g. Raspberry Pi OS)
```sh
sudo apt-get install python3-dev cmake
python -m pip install -r requirements.txt
```

#### Alpine Linux (e.g. Docker container)
```sh
apk add --no-cache cmake make g++ boost-dev
python -m pip install -r requirements.txt
```

### Install the library

```sh
python -m pip install .
```

Or for development:
```sh
python -m pip install -e .
```

## Usage

### Basic Python Usage

Assuming that pins are `ce_pin=25` and `csn_pin=0` and the id of the remote is `0xABCDEF` (3 byte long), start with:

```python
from xiaomi_lightbar import Lightbar
bar = Lightbar(25, 0, 0xABCDEF)
```

### Available Commands

The light bar remote has six operations:
- On/off, pressing the knob
- Higher and lower light brightness, turning the knob
- Colder and warmer color temperature, pressing and turning the knob
- Reset to medium brightness and warm color, long-pressing the knob

```python
bar.on_off()  # Turn on or off
bar.cooler()  # Cooler/bluer color
bar.warmer()  # Warmer/yellower color
bar.higher()  # Higher brightness
bar.lower()   # Lower brightness
bar.reset()   # Reset, medium brightness, warm color
```

The four turning operations also register the speed of change. Therefore, the four corresponding commands accept one optional numerical parameter, 1 to 15, that represent the change in each operation. The default value is 1, while 15 covers the full range of brightness or color temperature in just one operation.

```python
bar.cooler(15)
bar.warmer(3)
bar.higher(5)
bar.lower(4)
```

### Absolute Control

Set absolute brightness value (0 lowest, 15 highest):
```python
bar.brightness(4)   # Medium-low
bar.brightness(13)  # Rather high
```

Set absolute color temperature (0 warmest, 15 coldest):
```python
bar.color_temp(0)   # Warm white, 2700 K
bar.color_temp(8)   # Intermediate, cool white
bar.color_temp(15)  # Day light, 6500 K
```

### Finding Your Remote ID

The id of the remote can be extracted using the [scan_lightbar_remote.py](scripts/scan_lightbar_remote.py) script provided in the `scripts/` folder:

```sh
python scripts/scan_lightbar_remote.py
```

Run the script and operate your remote (turn the knob) near the nRF24L01. The script will capture packets and display the remote ID.

### Programming the Bar with an Arbitrary ID

If you cannot/do not want to capture your remote id, you can reprogram the bar with an arbitrary one. Choose an arbitrary id:

```python
bar = Lightbar(25, 0, 0x111111)
```

Unplug and plug the bar, and within 20 seconds run:

```python
bar.reset()
```

The bar will briefly flash, indicating successful programming. Note: The original remote will no longer work until you reprogram the bar again.

## MQTT Service for Home Assistant

### Home Assistant Configuration

Add the following to your `configuration.yaml` file in Home Assistant and restart:

```yaml
mqtt:
  - light:
      - name: "Xiaomi Lightbar"
        command_topic: "xiaomi/lightbar/control"
        payload_on: "ON"
        payload_off: "OFF"
        max_mireds: 370
        min_mireds: 153
        brightness_command_topic: "xiaomi/lightbar/brightness/set"
        color_temp_command_topic: "xiaomi/lightbar/temperature/set"
        brightness_value_template: "{{ value_json.brightness }}"
        color_temp_value_template: "{{ value_json.temp }}"
```

### Running the MQTT Subscriber

Run the MQTT subscriber script with the appropriate arguments:

```sh
python mqtt/subscriber.py \
  --broker homeassistant.local \
  --port 1883 \
  --username your_mqtt_username \
  --password your_mqtt_password \
  --topic xiaomi/lightbar \
  --ce_pin 25 \
  --csn_pin 0 \
  --remote_id 0xABCDEF
```

Arguments:
- `--broker` - MQTT Broker address (default: homeassistant.local)
- `--port` - MQTT Port (default: 1883)
- `--username` - MQTT Username (default: empty, no auth)
- `--password` - MQTT Password (default: empty, no auth)
- `--topic` - MQTT Topic prefix (default: xiaomi/lightbar)
- `--ce_pin` - CE Pin (default: 25)
- `--csn_pin` - CSN Pin (default: 0)
- `--remote_id` - Remote ID in hex format (default: 0xABCDEF)

If your MQTT broker has no password, keep username and password empty.

If everything is configured correctly, you should see a light entity named "Xiaomi Lightbar" in Home Assistant. You can now control your light bar from Home Assistant!

### Running as a systemd Service

To run the MQTT subscriber as a background service that starts automatically on boot, create a systemd service file.

Create a file `/etc/systemd/system/xiaomi-lightbar.service` with the following content (replace `{$USER}`, `{$MQTT_SERVER}`, `{$MQTT_PORT}`, `{$MQTT_USERNAME}`, `{$MQTT_PASSWORD}`, and `{$LIGHTBAR_ID}` with your actual values):

```ini
[Unit]
Description=Xiaomi Lightbar MQTT Subscriber
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/home/{$USER}/xiaomi-lightbar-via-nrf24
ExecStart=/usr/bin/python3 /home/{$USER}/xiaomi-lightbar-via-nrf24/mqtt/subscriber.py --broker {$MQTT_SERVER} --port {$MQTT_PORT} --username {$MQTT_USERNAME} --password {$MQTT_PASSWORD} --remote_id {$LIGHTBAR_ID}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```sh
sudo systemctl daemon-reload
sudo systemctl enable xiaomi-lightbar.service
sudo systemctl start xiaomi-lightbar.service
```

Check the status:

```sh
sudo systemctl status xiaomi-lightbar.service
```

View logs:

```sh
journalctl -u xiaomi-lightbar.service -f
```

## Scripts

### Test Lightbar

Test script to verify the lightbar is working:

```sh
python scripts/test_lightbar.py -i 0xABCDEF
```

Options:
- `-c, --channel` - RF channel (default: 6)
- `-p, --power` - Power level: MIN, LOW, HIGH, MAX (default: LOW)
- `-i, --id` - Remote ID in hex (default: 0xABCDEF)

### Scan Lightbar Remote

Capture and analyze packets from the original remote to extract the device ID:

```sh
python scripts/scan_lightbar_remote.py
```

Options:
- `-c, --channel` - RF channel (default: 6)
- `-p, --power` - Power level: MIN, LOW, HIGH, MAX (default: LOW)

## Technical Details

### Radio Configuration

- RF channels: 6, 15, 43, 68 (2406 MHz, 2415 MHz, 2443 MHz, 2468 MHz)
- Bitrate: 2 Mbps
- Modulation: GFSK (Gaussian frequency shift keying)
- Frequency hopping: Yes
- Each command is sent as a burst of 20 identical packets with 10ms delay

## Acknowledgments

This library is inspired and based on the excellent work by Alejandro García Lampérez in [xiaomi-lightbar-nrf24](https://github.com/lamperez/xiaomi-lightbar-nrf24).

## License

MIT License - See [LICENSE](LICENSE) file for details.