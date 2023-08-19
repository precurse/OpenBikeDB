# BikeDB
ESP32 Data logger for Schwinn IC4 (and probably other bikes as well)

Current Features:
- Displays power, cadence, HR, time, distance, etc.
- Display timeout to turn off if no bike detected for some time
- Automatically stream data to InfluxDB in 10s intervals

Todo Features:
- Auto upload of TCX files to online service, such as RUNALYZE or Strava
- Query and display any previous personal best outputs
- HR emulator to passthrough to other devices

## Hardware Required
- ESP32 board
- SSD1306 display
