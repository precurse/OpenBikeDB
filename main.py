import aioble
import binascii
import machine
import network
import ntptime
import ssd1306
import struct
import sys
import time
import ubluetooth as bluetooth
import urequests as requests
import uasyncio as asyncio
from collections import deque
from micropython import const
from machine import Pin, SoftI2C, RTC
from bikestats import BikeStats, SessionDone, SessionState
from config import *

sys.path.append("")

_NOTIFY_ENABLE = const(1)
LAST_BT_CON_T = None    # Track last time BT conn made
SERIAL_OUTPUT = False   # Output live metrics to serial. Useful for debugging
SCREEN_SLEEP_TIME = 300 # Time (in ms) to wait to sleep display if no BT conn
DATA_INTERVAL = 1       # Interval (seconds) to track data
INFLUX_INTERVAL = 5     # Interval (seconds) to send data (batched) to InfluxDB

def network_init(oled):
    global WIFI_SSID
    global WIFI_KEY
    global LAST_BT_CON_T

    station = network.WLAN(network.STA_IF)
    station.active(True)

    s = "Connecting to Wifi"
    print(s)
    oled_print(oled,s)

    if not station.isconnected():
        station.connect(WIFI_SSID, WIFI_KEY)
    else:
        print("Already connected")

    # Require Wifi to work
    while not station.isconnected():
        print('.', end='')
        time.sleep(0.5)

    print(f"Network config: {station.ifconfig()}")

    rtc = RTC()         # accurate time
    ntptime.settime()
    s = "Updated NTP"
    print(s)
    oled_print(oled,s)
    LAST_BT_CON_T = time.time()
    time.sleep(2)

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
            # org.bluetooth.service.fitness_machine
            fm_service = await connection.service(bluetooth.UUID(0x1826))
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

        try:
            await indoor_bike_data_char.subscribe(notify=True)
            t = await indoor_bike_data_char.descriptor(bluetooth.UUID(0x2902))
            await t.write(struct.pack("<H", _NOTIFY_ENABLE))
        except OSError:
            return

        print("Subscribed to Indoor Bike data. Waiting for data")
        while True:
            try:
                data = await indoor_bike_data_char.notified()
                await bike.parse_bike_data(data)
            except SessionDone:
                # Reset Bike Stats then disconnect
                # TODO: Fix BT disconnecting so we don't have to reboot
                machine.reset()
                # bike.reset_stats()
                #await device.disconnect()
            await asyncio.sleep_ms(0)

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
    global DATA_INTERVAL
    d = bs.data
    while True:
        if d['id'] != 0 and bs.session_state == SessionState.RUNNING:
            meas = {}
            meas['ts'] = time.time() + 946684800 # True UNIX timestamp

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
            q.append(meas)

            await asyncio.sleep(DATA_INTERVAL)
        else:
            # Check every 500ms for data if not started
            await asyncio.sleep_ms(500)

async def influx_task(q):
    global INFLUX_INTERVAL
    data = []
    while True:
        # Wait for at least one entry
        qlen = len(q)
        if qlen >= 1:
            # Grab 5 measurements to send
            for i in range(qlen):
                m = q.popleft()
                
                l = f'{INFLUX_DB},id={m["id"]} '
                l += f'power_max={m["pmax"]},power_last={m["pcur"]},power_avg={m["pavg"]},' 
                l += f'cadence_max={m["cadmax"]},cadence_last={m["cadcur"]},cadence_avg={m["cadavg"]},' 
                l += f'speed_max={m["smax"]},speed_last={m["scur"]},speed_avg={m["savg"]},distance={m["dist"]},' 
                l += f'calories={m["cals"]},duration={m["duration"]}' 

                # Check if HR present
                if m['hravg'] > 0:
                    l += f',hr_max={m["hrmax"]},hr_last={m["hrcur"]},hr_avg={m["hravg"]}' 

                l += f' {m["ts"]}'
                data.append(l)
            try:
                r = requests.post(f'http://{INFLUX_HOST}/api/v2/write?bucket={INFLUX_DB}&precision=s', data='\n'.join(data))
                r.close()
            except OSError:
                print("Failed to upload to influxdb")
                # Try again, so we don't lose metrics
                await asyncio.sleep(1)
                try:
                    r = requests.post(f'http://{INFLUX_HOST}/api/v2/write?bucket={INFLUX_DB}&precision=s', data='\n'.join(data))
                    r.close()
                except OSError:
                    print("Failed to retry upload to influxdb")
            finally:
                data = []

        # Run every set interval
        await asyncio.sleep(INFLUX_INTERVAL)

async def oled_task(bs,oled):
    # Loop through bike data
    data = bs.data
    while True:
        if data['id'] == 0:
            oled.fill(0)
            oled.text('Not started',0,0)
            oled.show()
        elif data['id'] != 0 and bs.session_state == SessionState.PAUSED:
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
                oled.poweron()
                oled_print(oled, "Found bike. Connecting...")
                LAST_BT_CON_T = time.time()
                # main loop
                await connect_bike(device, bs, oled)
        except aioble.DeviceDisconnectedError:
            pass
        except AttributeError:
            pass
        # Wait between connection attempts
        await asyncio.sleep(2)

async def serial_out_task(bs):
    while True:
        if bs.session_state == SessionState.RUNNING:
            speed = bs.data['speed_last']
            power = bs.data['power_last']
            rpm = bs.data['cadence_last']
            hr = bs.data['hr_last']

            output = "Speed {}".format(speed)
            output += "/ watts {}".format(power)
            output += "/ RPM {}".format(rpm)
            output += "/ HR {}".format(hr)

            output2 = "SESSION: "
            output2 += "Total Cals {}".format(bs.data['calories_tot'])
            output2 += "/ AVG Watts {}".format(bs.data['power_avg'])
            output2 += "/ AVG Speed {}".format(bs.data['speed_avg'])

            print(output2)
            print(output, end="\r")
        elif bs.session_state == SessionState.PAUSED:
            print("Session paused...", end='\r')
        elif bs.session_state == SessionState.NOT_STARTED:
            print("Session not started yet", end='\r')

        await asyncio.sleep(1)

def set_global_exception():
    def handle_exception(loop, context):
        sys.print_exception(context["exception"])
        sys.exit()
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

async def main():
    global SERIAL_OUTPUT
    set_global_exception()  # Debug aid
    oled = oled_init()
    network_init(oled)
    bs = BikeStats()
    q = deque((),20)

    t1 = asyncio.create_task(bike_task(bs,oled))
    t2 = asyncio.create_task(oled_task(bs,oled))
    t3 = asyncio.create_task(data_queue_task(bs,q))
    t4 = asyncio.create_task(influx_task(q))
    if SERIAL_OUTPUT:
        t5 = asyncio.create_task(serial_out_task(bs))
        await asyncio.gather(t1,t2,t3,t4,t5)
    else:
        await asyncio.gather(t1,t2,t3,t4)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
