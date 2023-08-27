import ubluetooth as bluetooth
import binascii
import network
import struct
import sys
import time
import ntptime
import aioble
import urequests as requests
import uasyncio as asyncio
import ssd1306
from collections import deque
from micropython import const
from machine import Pin, SoftI2C, RTC
from bikestats import BikeStats, SessionDone
from config import *

sys.path.append("")

_NOTIFY_ENABLE = const(1)
LAST_BT_CON_T = time.time()
# How long to wait before sleeping display
SCREEN_SLEEP_TIME = 300

def network_init():
    global WIFI_SSID
    global WIFI_KEY

    station = network.WLAN(network.STA_IF)
    station.active(True)
    print("Connecting to Wifi")

    if not station.isconnected():
        station.connect(WIFI_SSID, WIFI_KEY)
    else:
        print("Already connected")

    # Require Wifi to work
    while not station.isconnected():
        print('.', end='')
        time.sleep(0.5)

    print(f"Network config: {station.ifconfig()}")
    rtc = RTC()
    ntptime.settime()
    print("Updated NTP")

async def find_bike():
    global BTLE_NAME
    print("Beginning scan")
    async with aioble.scan(5000, interval_us=30000, window_us=30000, active=True) as scanner:
        async for result in scanner:
            if result.name():
                # Fitness machine is 0x1826
                # 0x180a is the only option without a connection
                if result.name().startswith(BTLE_NAME) and bluetooth.UUID(0x180a) in result.services():
                    print(result, result.name(), result.rssi, result.services())
                    return result.device
    return None

async def connect_bike(device, bike, oled):
    try:
        print("Connecting to: ",device)
        connection = await device.connect()
        
    except asyncio.TimeoutError:
        print('Timeout during connection')
        return

    async with connection:
        try:
            fm_service = await connection.service(bluetooth.UUID(0x1826))
            # for f in await fm_service.characteristics():
            #     print(f)
            indoor_bike_data_char = await fm_service.characteristic(bluetooth.UUID(0x2AD2))
        except asyncio.TimeoutError:
            print("Timeout discovering services/characteristics")
            return
        except AttributeError:
            print("Error getting Characteristic")
            return
        except OSError:
            print("OSError")
            return
        except Exception as e:
            print("Unhandled exception when connecting to BLE:")
            print(e)
            return

        print("Fitness service: ", fm_service)
        print("Fitness char: ", indoor_bike_data_char)

        await indoor_bike_data_char.subscribe(notify=True)
        t = await indoor_bike_data_char.descriptor(bluetooth.UUID(0x2902))
        await t.write(struct.pack("<H", _NOTIFY_ENABLE))
        ##### self.bt.gattc_write(0, 49, struct.pack('<h', _NOTIFY_ENABLE), 1)

        print("Subscribed to Indoor Bike data. Waiting for data")
        while True:
            try:
                data = await indoor_bike_data_char.notified()
                await bike.parse_bike_data(data)
            except SessionDone:
                # Reset Bike Stats
                bike.reset_stats()
                await bike.disconnect()
                return

def oled_print(oled, text):
    oled.fill(0)
    oled.text(text, 0, 0)
    oled.show()

def oled_init():
    i2c = SoftI2C(scl=Pin(22), sda=Pin(21))
    oled_width = 128
    oled_height = 64
    oled = ssd1306.SSD1306_I2C(oled_width, oled_height, i2c)
    return oled

async def data_queue_task(bs,q):
    """
    Used to put metrics onto a deque for InfluxDB consumer
    """
    d = bs.data
    while True:
        if d['id'] != 0 and not bs.session_paused:
            meas = {}
            meas['id'] = d['id']
            meas['pmax'] = d['power_max']
            meas['pcur'] = d['power_last']
            meas['pavg'] = d['power_avg']
            meas['cadmax'] = d['cadence_max']
            meas['cadcur'] = d['cadence_last']
            meas['cadavg'] = d['cadence_avg']
            meas['smax'] = d['speed_max']
            meas['scur'] = d['speed_last']
            meas['savg'] = d['speed_avg']
            meas['hrmax'] = d['hr_max']
            meas['hrcur'] = d['hr_last']
            meas['hravg'] = d['hr_avg']
            meas['cals'] = d['calories_tot']
            meas['duration'] = d['duration']
            meas['dist'] = d['dist_tot']
            # True UNIX timestamp
            meas['ts'] = time.time() + 946684800
            q.append(meas)
            # Run every 1 second
            await asyncio.sleep_ms(1000)
        else:
            # Check every 500ms for data if not started
            await asyncio.sleep_ms(500)

async def influx_task(bs,q):
    d = bs.data
    # q.popleft()
    while True:
        # Wait until deque has 5 readings
        if len(q) >= 5:
            data = ""
            # Grab 5 measurements to send
            for i in range(5):
                m = q.popleft()
                data += f'{INFLUX_DB},id={m["id"]} '
                data += f'power_max={m["pmax"]},power_last={m["pcur"]},power_avg={m["pavg"]},' 
                data += f'cadence_max={m["cadmax"]},cadence_last={m["cadcur"]},cadence_avg={m["cadavg"]},' 
                data += f'speed_max={m["smax"]},speed_last={m["scur"]},speed_avg={m["savg"]},distance={m["dist"]},' 
                data += f'calories={m["cals"]},duration={m["duration"]}' 

                # Check if HR present
                if m['hravg'] > 0:
                    data += f',hr_max={m["hrmax"]},hr_last={m["hrcur"]},hr_avg={m["hravg"]}' 

                data += f' {m["ts"]}'
                # New line at end of each measurement, except the last one
                if i < 4:
                    data += '\n'

            try:
                requests.post(f'http://{INFLUX_HOST}/api/v2/write?bucket={INFLUX_DB}&precision=s', data=data)
            except OSError:
                print("Failed to upload to influxdb")
                # Try again, so we don't lose metrics
                await asyncio.sleep_ms(1)
                try:
                    requests.post(f'http://{INFLUX_HOST}/api/v2/write?bucket={INFLUX_DB}&precision=s', data=data)
                except OSError:
                    print("Failed to retry upload to influxdb")
            finally:
                del(data)

        # Run every 1 second
        await asyncio.sleep(1)

async def oled_task(bs,oled):
    # Loop through bike data
    data = bs.data
    while True:
        if data['id'] == 0:
            oled.fill(0)
            oled.text('Not started',0,0)
            oled.show()
        elif data['id'] != 0 and bs.session_paused:
            oled.fill(0)
            oled.text('Session paused',0,0)
            oled.show()
        else:
            cad = int(data['cadence_last'])
            cad_max = int(data['cadence_max'])
            sl = int(data['speed_last']/100)
            sa = int(data['speed_avg']/100)
            cal = int(data['calories_tot'])
            hc = int(data['hr_last'])
            hm = int(data['hr_max'])
            pm = int(data['power_max'])
            pa = int(data['power_avg'])
            pl = int(data['power_last'])
            t = int(data['duration'])
            d = int(data['dist_tot'])
            
            oled.fill(0)
            oled.text(f"RPM {cad:<3} Rm {cad_max}", 0, 0)
            oled.text(f"S {sl} Sa {sa}", 0, 10)
            oled.text(f"Hc {hc} Hm {hm}", 0, 20)
            oled.text(f"Cal {cal:<3} T {t}", 0, 30)
            oled.text(f"Wm {pm:<3} Wa {pa}", 0, 40)
            oled.text(f"Wc {pl:<3} D {d}", 0, 50)
            oled.show()

        # Update every 500ms
        await asyncio.sleep_ms(500)

async def bike_task(bs,oled):
    global LAST_BT_CON_T
    global SCREEN_SLEEP_TIME
    while True:
        try:
            oled_print(oled, "Finding Bike...")
            device = await find_bike()

            if not device:
                print("Not found")
                oled_print(oled, "Bike not found...")
                if (time.time() - LAST_BT_CON_T) > SCREEN_SLEEP_TIME:
                    oled.poweroff()
            else:
                oled_print(oled, "Found bike. Connecting...")
                LAST_BT_CON_T = time.time()
                oled.poweron()
                # main loop
                await connect_bike(device, bs, oled)
        except aioble.DeviceDisconnectedError:
            pass
        except AttributeError:
            pass
        # Wait 5s between connection attempts
        await asyncio.sleep(5)

def set_global_exception():
    def handle_exception(loop, context):
        import sys
        sys.print_exception(context["exception"])
        sys.exit()
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

async def main():
    set_global_exception()  # Debug aid
    network_init()
    oled = oled_init()
    bs = BikeStats()
    q = deque((),20)

    t1 = asyncio.create_task(bike_task(bs,oled))
    t2 = asyncio.create_task(oled_task(bs,oled))
    t3 = asyncio.create_task(data_queue_task(bs,q))
    t4 = asyncio.create_task(influx_task(bs,q))
    await asyncio.gather(t1,t2,t3,t4)

try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
