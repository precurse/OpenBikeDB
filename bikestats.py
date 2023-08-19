import ubluetooth as bluetooth
import binascii
import network
import random
import sys
import time
import ntptime
import aioble
import ssd1306
import uasyncio as asyncio
import urequests
from machine import Pin, SoftI2C
from micropython import const

"""
TODO:
- Fix BLE disconnecting during session to autoresume
"""

class SessionDone(Exception):
    pass

class BikeStats():
    def __init__(self):
        self._connected = False

        # Metric format
        self.reset_stats()

        self.session_started = False
        self.session_start_time = None
        self.session_paused = False
        self.session_paused_time = None

        self.influx_last_send = 0

    def reset_stats(self):
        # Reset stats
        self.data = {
            'id':0,
            'power_max':0,
            'power_avg':0,
            'power_last':0,
            'power_cnt':0,
            'speed_max':0,
            'speed_avg':0,
            'speed_last':0,
            'speed_cnt':0,
            'hr_max':0,
            'hr_avg':0,
            'hr_last':0,
            'hr_cnt':0,
            'cadence_max':0,
            'cadence_avg':0,
            'cadence_last':0,
            'cadence_cnt':0,
            'dist_tot':0,
            'duration':0,
            'calories_tot':0,
            'paused_t':0,
        }

    def update_data(self, name, val):
        try:
            int(val)
        except ValueError:
            return
        name_max = name + '_max'
        name_avg = name + '_avg'
        name_cnt = name + '_cnt'
        name_last = name + '_last'

        if not self.data[name_max] or self.data[name_max] < val:
            self.data[name_max] = val
        self.data[name_avg] = ((self.data[name_avg] * self.data[name_cnt] + val))/(self.data[name_cnt]+1)
        self.data[name_last] = val
        self.data[name_cnt] += 1

    def get_unix_time(self):
        return time.time() + 946684800

    def update_power(self, val):
        self.update_data('power', val)

    def update_speed(self, val):
        self.update_data('speed', val)

    def update_cadence(self, val):
        self.update_data('cadence', val)

    def update_hr(self, val):
        self.update_data('hr', val)

    def update_dist(self, val):
        self.data['dist_tot'] = val

    def update_duration(self):
        self.data['duration'] = time.time() - self.session_start_time - self.data['paused_t']

    def update_calories(self, val):
        self.data['calories_tot'] = val

    def update_distance(self):
        # Get average speed and duration
        avg_speed = self.data['speed_avg']
        duration = self.data['duration'] - self.data['paused_t']

        # Speed km/h * seconds / (60*60)
        self.data['dist_tot'] = avg_speed * duration / (60*60)

    def update_paused_time(self):
        paused_time_elapsed = time.time() - self.session_paused_time
        self.data['paused_t'] += paused_time_elapsed

    def start_session(self):
        self.session_started = True
        self.session_start_time = time.time()
        # micropython uses EPOCH of 2000-01-01, so add that for the true EPOCH
        self.data['id'] = self.session_start_time + 946684800
        #self._oled.fill(0)

    def end_session(self):
        # Update paused time if session was paused, since there is 3m of inactivity before session ends
        if self.session_paused:
            self.update_paused_time()
            self.update_duration()

        # Send final data then end session
        raise SessionDone

    def resume_session(self):
        self.session_paused = False
        self.update_paused_time()
        self.session_paused_time = None
        print(f"Resumed after {paused_time_elapsed} seconds")

    def pause_session(self):
        self.session_paused = True
        self.session_paused_time = time.time()

    def get_calories(self):
        # energy (kcal) = avg power (Watts) X duration (hours) X 3.6
        # 205 Watts * 1.5 hours * 3.6 = 1,107 kcal
        time_elapsed = self.data['duration'] - self.data['paused_t']
        kcal = self.data['power_avg'] * time_elapsed/3600 * 3.6
        return kcal

    async def parse_bike_data(self, data):
            flags = int.from_bytes(data, 'little')
            power = None
            speed = None
            output = ""

            # is_more_data is actually current speed in km/h
            is_more_data = 0x0001
            is_instantaneous_cadence_present_mask = 0x0002
            is_average_speed_present_mask = 0x0004
            is_average_cadence_present_mask = 0x0008
            is_total_distance_present_mask = 0x0010
            is_resistance_level_present = 0x0020
            is_instantaneous_power_present = 0x0040
            is_average_power_present = 0x0080
            is_expended_energy_present = 0x0100
            is_heart_rate_present = 0x0200
            is_metabolic_equivalent_present = 0x0400
            is_elapsed_time_present = 0x0800
            is_remaining_time_present = 0x1000

            # Header is 2 bytes (uint16)
            measurement_byte_offset = 2

            # Instantaneous Speed
            ## This is inversed i guess.. to work
            if not flags & is_more_data:
                speed = int.from_bytes(data[measurement_byte_offset:measurement_byte_offset+2], 'little')
                measurement_byte_offset += 2

                # Threshold to consider training paused
                speed_threshold = 0 

                if speed != speed_threshold and not self.session_started:
                    self.start_session()
                elif speed != speed_threshold and self.session_paused:
                    # Continuing a paused session
                    self.resume_session()
                elif speed == speed_threshold and self.session_started and not self.session_paused:
                    self.pause_session()
                elif speed == speed_threshold and self.session_started and self.session_paused:
                    print("Session paused...", end='\r')
                    # If session is paused for 3 minutes, end it
                    if time.time() - self.session_paused_time >= 60*3:
                        self.end_session()
                elif self.session_started and not self.session_paused:
                    self.session_paused = False
                    # Session is going!
                    #print("C1: Instantaneous Speed: {}".format(speed))
                    self.update_speed(speed)
                    output += "Speed {}".format(speed)
                else:
                    print("Session not started yet", end='\r')
                    return

            if flags & is_instantaneous_cadence_present_mask:
                print("is_instantaneous_cadence_present_mask")

            # This is actually Instantaneous Cadence
            if flags & is_average_speed_present_mask:
                cadence = int.from_bytes(data[measurement_byte_offset:measurement_byte_offset + 2], 'little')
                measurement_byte_offset += 2

                if self.session_started and not self.session_paused:
                    rpm = cadence/2
                    output += "/ RPM {}".format(rpm)
                    self.update_cadence(rpm)

            if flags & is_average_cadence_present_mask:
                print("is_average_cadence_present_mask")
            if flags & is_total_distance_present_mask:
                print("is_total_distance_present_mask")
            if flags & is_resistance_level_present:
                print("is_resistance_level_present")
            if flags & is_instantaneous_power_present:
                power = int.from_bytes(data[measurement_byte_offset:measurement_byte_offset + 2], 'little')
                measurement_byte_offset += 2
                self.update_power(power)
                self.update_duration()
                self.update_distance()
                self.update_calories(self.get_calories())

                if self.session_started and not self.session_paused:
                    output += "/ watts {}".format(power)

            if flags & is_average_power_present:
                print("is_average_power_present")
            if flags & is_expended_energy_present:
                print("is_expended_energy_present")
            if flags & is_heart_rate_present:
                hr = int.from_bytes(data[measurement_byte_offset:measurement_byte_offset + 1], 'little')
                measurement_byte_offset += 1

                if self.session_started and not self.session_paused:
                    if hr <= 0:
                        hr = "n/a"
                    else:
                        self.update_hr(hr)
                    output += "/ HR {}".format(hr)

            if flags & is_metabolic_equivalent_present:
                print("is_metabolic_equivalent_present")
            if flags & is_elapsed_time_present:
                print("is_elapsed_time_present")
            if flags & is_remaining_time_present:
                print("is_remaining_time_present")

            # Only print session data if started
            if self.session_started and not self.session_paused:
                self.print_serial_data()
                print(output, end="\r")

    def print_serial_data(self):
        output = "SESSION: "
        output += "Total Cals {}".format(self.data['calories_tot'])
        output += "/ AVG Watts {}".format(self.data['power_avg'])
        output += "/ AVG Speed {}".format(self.data['speed_avg'])
        # print(output)
        # Print data to console every 10 seconds
        if time.time() % 10 == 0:
            print(f"Raw: {self.data}")
