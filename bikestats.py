import struct
import time
from ucollections import OrderedDict

class SessionDone(Exception):
    pass

# Workaround for lack of enum in MicroPython
class SessionState(object):
    NOT_STARTED = 1
    RUNNING = 2
    PAUSED = 3
    ENDED = 4

class BikeStats():
    def __init__(self):
        self._connected = False
        self.session_state = SessionState.NOT_STARTED
        self.reset_stats()
        self.session_started = False
        self.session_start_time = None
        self.session_paused_time = None
        self.parse_struct_str = None

    def reset_stats(self):
        # Used to both initialize and reset data
        self.session_state = SessionState.NOT_STARTED
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

    def update_power(self, val):
        self.update_data('power', val)

    def update_speed(self, val):
        self.update_data('speed', val)

    def update_cadence(self, val):
        self.update_data('cadence', val)

    def update_hr(self, val):
        # 0 means not present or not working
        if val > 0:
            self.update_data('hr', val)

    def update_duration(self):
        # Ignore paused time
        self.data['duration'] = time.time() - self.session_start_time - self.data['paused_t']

    def update_calories(self):
        val = self.get_calories()
        self.data['calories_tot'] = val

    def update_distance(self):
        # Get average speed and duration
        speed_avg = self.data['speed_avg']
        duration = self.data['duration']

        # Speed km/h * seconds / (60*60)
        self.data['dist_tot'] = speed_avg * duration / (60*60)

    def update_paused_time(self):
        # Tracks the total time a session has been paused
        paused_time_elapsed = time.time() - self.session_paused_time
        self.data['paused_t'] += paused_time_elapsed

    def start_session(self):
        self.session_started = True
        self.session_start_time = time.time()
        # micropython uses EPOCH of 2000-01-01, so add that for the true EPOCH
        self.data['id'] = self.session_start_time + 946684800
        self.session_state = SessionState.RUNNING

    def end_session(self):
        # Update paused time if session was paused, since there is 3m of inactivity before session ends
        if self.session_state == SessionState.PAUSED:
            self.update_paused_time()
            self.update_duration()

        # Send final data then end session
        self.session_state = SessionState.ENDED
        raise SessionDone

    def resume_session(self):
        self.session_state = SessionState.RUNNING
        self.update_paused_time()
        self.session_paused_time = None

    def pause_session(self):
        self.session_state = SessionState.PAUSED
        self.session_paused_time = time.time()

    def get_calories(self):
        # Returns kcal based on average power and duration
        # energy (kcal) = avg power (Watts) X duration (hours) X 3.6
        time_elapsed = self.data['duration']
        kcal = self.data['power_avg'] * time_elapsed/3600 * 3.6
        return kcal

    async def parse_header(self, data):
        """
        BLE sends a long byte string containing all measurements, along with a "flag" header (uint16)
        The flags (header) must first be checked to see what types of data is included
        More here: (Test Suite pg 43) https://www.bluetooth.com/specifications/specs/fitness-machine-service-1-0/
        GATT fields are always (or at least should always be) little-endian
        Test data b'D\x02D\x0c\x8c\x00\x94\x00\x00'
        """
        # 4.9.1.1 Flags Field (page 43)
        is_more_data = 0x0001
        is_average_speed_present_mask = 0x0002
        is_instantaneous_cadence_present_mask = 0x0004
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

        flags = struct.unpack('<H', data[:2])[0]

        # Unpacked data in order
        self.udata = OrderedDict()

        # Little endian
        unpack_str = "<"
        if not flags & is_more_data:
            unpack_str += "H"
            self.udata['speed'] = None
        # if flags & is_average_speed_present_mask:
        #     pass
        if flags & is_instantaneous_cadence_present_mask:
            unpack_str += "H"
            self.udata['raw_cadence'] = None
        # if flags & is_average_cadence_present_mask:
        #     pass
        # if flags & is_total_distance_present_mask:
        #     pass
        # if flags & is_resistance_level_present:
        #     pass
        if flags & is_instantaneous_power_present:
            unpack_str += "H"
            self.udata['power'] = None
        # if flags & is_average_power_present:
        #     pass
        # if flags & is_expended_energy_present:
        #     pass
        if flags & is_heart_rate_present:
            unpack_str += "B"
            self.udata['hr'] = None
        # if flags & is_metabolic_equivalent_present:
        #     pass
        # if flags & is_elapsed_time_present:
        #     pass
        # if flags & is_remaining_time_present:
        #     pass

        self.parse_struct_str = unpack_str

    async def parse_bike_data(self, data):
        if not self.parse_struct_str:
            await self.parse_header(data)

        # Ignoring first 2 bytes (flags header)
        raw_data = data[2:]
        try:
            parsed_data  = struct.unpack(self.parse_struct_str, raw_data)
        except struct.error as e:
            print(f"Failed to unpack data as expected: {e}")
            return

        # Capture parsed data in order
        for idx,d in enumerate(parsed_data):
            k = list(self.udata)[idx]
            self.udata[k] = d

        # Begin Session handling
        if self.udata['speed'] > 0 and self.session_state == SessionState.NOT_STARTED:
            self.start_session()
        elif self.udata['speed'] > 0 and self.session_state == SessionState.PAUSED:
            # Continuing a paused session
            self.resume_session()
        elif self.udata['speed'] == 0 and self.session_state == SessionState.RUNNING:
            self.pause_session()
            return
        elif self.udata['speed'] == 0 and self.session_state == SessionState.PAUSED:
            # If session is paused for 3 minutes, end it
            if time.time() - self.session_paused_time >= 3*60:
                self.end_session()
            return
        elif self.session_state == SessionState.NOT_STARTED:
            return

        # Update metrics if they exist, otherwise ignore
        for func, val in (
            (self.update_speed, self.udata['speed']),
            (self.update_cadence, self.udata['raw_cadence']/2),
            (self.update_power, self.udata['power']),
            (self.update_hr, self.udata['hr'])
        ):
            try:
                func(val)
            except KeyError:
                break

        # Run metrics calculations
        self.update_duration()
        self.update_distance()
        self.update_calories()
