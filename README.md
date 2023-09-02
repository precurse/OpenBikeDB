# BikeDB
ESP32 Data logger for Schwinn IC4 (and probably other bikes as well)

## Background
I created this project initially as a way to unlock certain metrics, such as power output,
unavailable to me. Also, the RPM gauge on the Schwinn IC4 display sucks, so I wanted a 
proper digital metric to see my current cadence.

The idea of this project is to not require a mobile app to function, and works in the background
to track (and upload) your metrics. The last thing I enjoy doing is messing around with Bluetooth
device pairing just before I want to workout.

## Current Features:
- Automatically connecting to Bluetooth-enabled bike
- Displays power, cadence, HR, time, distance, etc.
- Automatic LCD power-off if no bike detected for some time
- Per-second metrics are sent to InfluxDB
- Track current, average, and maximum metrics

## Planned Features:
- 3D printed case to mount to bike
- Auto upload of TCX files to online service, such as RUNALYZE or Strava
- Query and display any previous personal best outputs
- HR emulator to passthrough to other devices

## Hardware Required
- ESP32 board
- SSD1306 display
- Bluetooth Low-Energy enabled spin bike (like the Schwinn IC4)
- A calibrated Schwinn IC4 as per [page 18](https://download.nautilus.com/supportdocs/AM_OM/Bowflex/BFX.C6.SCH.IC4.IC8.SM.pdf) of the service manual
