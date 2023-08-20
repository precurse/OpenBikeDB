import ubluetooth as bluetooth
import binascii
import network
import struct
import random
import sys
import time
import ntptime
import aioble
import urequests as requests
import uasyncio as asyncio
import ssd1306
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
                return
            #print(indoor_bike_data_char.read())
            #await asyncio.sleep_ms(1000)

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

async def influx_task(bs):
    d = bs.data
    while True:
        if d['id'] != 0:
            i = d['id']
            pmax = d['power_max']
            pcur = d['power_last']
            pavg = d['power_avg']
            cadmax = d['cadence_max']
            cadcur = d['cadence_last']
            cadavg = d['cadence_avg']
            smax = d['speed_max']
            scur = d['speed_last']
            savg = d['speed_avg']
            hrmax = d['hr_max']
            hrcur = d['hr_last']
            hravg = d['hr_avg']
            cals = d['calories_tot']
            duration = d['duration']
            dist = d['dist_tot']
            # True UNIX timestamp
            ts = time.time() + 946684800

            data = f'{INFLUX_DB},id={i} '
            data += f'power_max={pmax},power_last={pcur},power_avg={pavg},' 
            data += f'cadence_max={cadmax},cadence_last={cadcur},cadence_avg={cadavg},' 
            data += f'speed_max={smax},speed_last={scur},speed_avg={savg},distance={dist},' 
            data += f'calories={cals},duration={duration}' 

            # Check if HR present
            if d['hr_cnt'] > 0:
                data += f',hr_max={hrmax},hr_last={hrcur},hr_avg={hravg}' 

            data += f' {ts}'

            try:
                requests.post(f'http://{INFLUX_HOST}/api/v2/write?bucket={INFLUX_DB}&precision=s', data=data)
            except OSError:
                print("Failed to upload to influxdb")

            # Run every 10 seconds
            await asyncio.sleep_ms(10000)
        else:
            # Recheck every 1/2 second for data
            await asyncio.sleep_ms(500)

async def oled_task(bs,oled):
    # Loop through bike data
    data = bs.data
    while True:
        if data['id'] == 0:
            oled.fill(0)
            oled.text('Not started',0,0)
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
        await asyncio.sleep_ms(5000)

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

    t1 = asyncio.create_task(bike_task(bs,oled))
    t2 = asyncio.create_task(oled_task(bs,oled))
    t3 = asyncio.create_task(influx_task(bs))
    await asyncio.gather(t1,t2,t3)

try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
