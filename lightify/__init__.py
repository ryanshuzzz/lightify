#!/usr/bin/python
#
# Copyright 2014 Mikael Magnusson
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

#
# WIP Python module for Osram lightify
# Communicates with a gateway connected to the same LAN via TCP port 4000
# using a binary protocol
#

#
# TODO: Support for motion sensors
#

import binascii
import logging
import socket
import struct
import threading
from collections import defaultdict
from enum import Enum

__version__ = '1.0.7.0'
MODULE = __name__
PORT = 4000

COMMAND_ALL_LIGHT_STATUS = 0x13
COMMAND_GROUP_LIST = 0x1e
COMMAND_SCENE_LIST = 0x1f
COMMAND_LUMINANCE = 0x31
COMMAND_ONOFF = 0x32
COMMAND_TEMP = 0x33
COMMAND_COLOUR = 0x36
COMMAND_ACTIVATE_SCENE = 0x52
COMMAND_LIGHT_STATUS = 0x68

# Commands
# 13 all light status (returns list of light address, light status, light name)
# 1e group list (returns list of group indices and group names)
# 1f scene list (returns list of scene indices and scene names)
# 31 set group luminance
# 32 set group onoff
# 33 set group temp
# 36 set group colour
# 52 activate scene
# 68 light status (returns light address and light status if reachable)

FLAG_LIGHT = 0x00
FLAG_GLOBAL = 0x02

DEFAULT_ALPHA = 0xff
DEFAULT_LUMINANCE = 1
MIN_TEMPERATURE = 2700
MAX_TEMPERATURE = 6500
MAX_LUMINANCE = 100
MAX_COLOUR = 255
LAST_SEEN_DURATION_MINUTES = 5

GATEWAY_TIMEOUT_SECONDS = 10


class DeviceSubType(Enum):
    """ device sub type
        list of known device ids may be incomplete!
    """
    LIGHT_NON_SOFTSWITCH = 1
    LIGHT_TUNABLE_WHITE = 2
    LIGHT_FIXED_WHITE = 4
    LIGHT_RGB = 10
    PLUG = 16
    MOTIONSENSOR = 32
    SWITCH_TWO_BUTTONS = 64
    SWITCH_FOUR_BUTTONS = 65
    SWITCH_UNKNOWN1 = 66 # not sure atm if these IDs really exist
    SWITCH_UNKNOWN2 = 67
    SWITCH_UNKNOWN3 = 68

    @classmethod
    def has_value(cls, value):
        """
        :return: whether enum value exists or not (true/false)
        """
        return any(value == item.value for item in cls)


class DeviceType(Enum):
    """ generalized device type
    """
    LIGHT = 1
    PLUG = 2
    MOTIONSENSOR = 3
    SWITCH = 4


DEVICESUBTYPE = defaultdict(lambda: DeviceSubType.LIGHT_RGB,
                            {item.value:item for item in DeviceSubType})
DEVICETYPE = defaultdict(lambda: DeviceType.LIGHT,
                         {16: DeviceType.PLUG, 32: DeviceType.MOTIONSENSOR,
                          64: DeviceType.SWITCH, 65: DeviceType.SWITCH,
                          66: DeviceType.SWITCH, 67: DeviceType.SWITCH,
                          68: DeviceType.SWITCH})


class Scene:
    """ representation of a scene
    """
    def __init__(self, conn, idx, name):
        """
        :param conn: Lightify object
        :param idx: index of the scene provided by the gateway
        :param name: scene name
        """
        self.__conn = conn
        self.__idx = idx
        self.__name = name

    def name(self):
        """
        :return: scene name
        """
        return self.__name

    def idx(self):
        """
        :return: index of the scene provided by the gateway
        """
        return self.__idx

    def activate(self):
        """ activate the scene

        :return:
        """
        command = self.build_command(COMMAND_ACTIVATE_SCENE, '')
        self.__conn.send(command)

    def build_command(self, command, data):
        """ build a scene command

        :param command: command id (1 byte)
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        return self.__conn.build_command(command, self.__idx, data)

    def __str__(self):
        return '<scene %s: %s>' % (self.__idx, self.__name)


class Light:
    """ class for controlling a single light source
    """

    def __init__(self, conn, addr, type_id):
        """
        :param conn: Lightify object
        :param addr: mac address of the light
        :param type_id: original device type id as returned by gateway
        """
        devicesubtype = DEVICESUBTYPE[type_id]
        devicetype = DEVICETYPE[type_id]

        self.__conn = conn
        self.__addr = addr
        self.__name = ''
        self.__reachable = True
        self.__last_seen = 0
        self.__on = False
        self.__groups = []
        self.__version = ''
        self.__deleted = False
        self.__type_id = type_id
        self.__devicesubtype = devicesubtype
        self.__devicetype = devicetype

        if devicetype in (DeviceType.MOTIONSENSOR, DeviceType.SWITCH):
            self.__lum = 0
            self.__temp = 0
            self.__red = 0
            self.__green = 0
            self.__blue = 0
            self.__supported_features = ()
        else:
            self.__lum = MAX_LUMINANCE
            self.__temp = MIN_TEMPERATURE
            self.__red = MAX_COLOUR
            self.__green = MAX_COLOUR
            self.__blue = MAX_COLOUR

            if devicetype == DeviceType.PLUG:
                self.__supported_features = ('on',)
            elif devicesubtype in (DeviceSubType.LIGHT_NON_SOFTSWITCH,
                                   DeviceSubType.LIGHT_FIXED_WHITE):
                self.__supported_features = ('on', 'lum')
            elif devicesubtype == DeviceSubType.LIGHT_TUNABLE_WHITE:
                self.__supported_features = ('on', 'lum', 'temp')
            else:
                self.__supported_features = ('on', 'lum', 'temp', 'rgb')

    def name(self):
        """
        :return: name of the light
        """
        return self.__name

    def addr(self):
        """
        :return: mac address of the light
        """
        return self.__addr

    def reachable(self):
        """
        :return: true if the light is reachable, false otherwise
        """
        return self.__reachable

    def last_seen(self):
        """
        :return: time since last seen by gateway in minutes
        """
        return self.__last_seen

    def on(self):
        """
        :return: true if the status of the light is on, false otherwise
        """
        return self.__on

    def lum(self):
        """
        :return: luminance (brightness)
        """
        return self.__lum

    def temp(self):
        """
        :return: colour temperature in kelvin
        """
        return self.__temp

    def red(self):
        """
        :return: amount of red
        """
        return self.__red

    def green(self):
        """
        :return: amount of green
        """
        return self.__green

    def blue(self):
        """
        :return: amount of blue
        """
        return self.__blue

    def rgb(self):
        """
        :return: tuple containing (red, green, blue)
        """
        return self.__red, self.__green, self.__blue

    def type_id(self):
        """
        :return: original device type id as returned by gateway
        """
        return self.__type_id

    def devicesubtype(self):
        """
        :return: device sub type (DeviceSubType object)
        """
        return self.__devicesubtype

    def devicetype(self):
        """
        :return: generalized device type (DeviceType object)
        """
        return self.__devicetype

    def groups(self):
        """
        :return: list of associated group indices
        """
        return self.__groups

    def version(self):
        """
        :return: firmware version
        """
        return self.__version

    def supported_features(self):
        """
        :return: tuple of supported features (on, lum, temp, rgb)
        """
        return self.__supported_features

    def deleted(self):
        """
        :return: whether the light is deleted from gateway or not
        """
        return self.__deleted

    def mark_deleted(self):
        """ mark the light as deleted from gateway
        """
        self.__deleted = True

    def update_status(self, reachable, last_seen, on, lum, temp, red, green,
                      blue, name, groups, version):
        """ update internal representation
            does not send out a command to the light source!

        :param reachable: if the light is reachable or not
        :param last_seen: time since last seen by gateway
        :param on: if the light is on or off
        :param lum: luminance (brightness)
        :param temp: colour temperature
        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param name: name of the light
        :param groups: list of associated group indices
        :param version: firmware version
        :return:
        """
        self.__reachable = bool(reachable)
        self.__last_seen = last_seen * LAST_SEEN_DURATION_MINUTES
        self.__name = name
        self.__groups = groups
        self.__version = version

        if 'on' in self.__supported_features:
            self.__on = bool(on)

        if 'lum' in self.__supported_features:
            self.__lum = lum

        if 'temp' in self.__supported_features:
            self.__temp = temp

        if 'rgb' in self.__supported_features:
            self.__red = red
            self.__green = green
            self.__blue = blue

    def set_onoff(self, on, send=True):
        """ set on/off

        :param on: true/false
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if not 'on' in self.__supported_features:
            return

        on = bool(on)
        self.__on = on
        if on and self.__lum == 0:
            self.__lum = DEFAULT_LUMINANCE

        if send:
            command = self.__conn.build_onoff(self, on)
            self.__conn.send(command)

    def set_luminance(self, lum, time, send=True):
        """ set luminance (brightness)

        :param lum: luminance (brightness). if 0, the light is turned off.
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if not 'lum' in self.__supported_features:
            return

        lum = min(MAX_LUMINANCE, lum)
        self.__lum = lum
        if lum > 0:
            self.__lum = lum
            self.__on = True
        elif lum == 0:
            self.__lum = DEFAULT_LUMINANCE
            self.__on = False

        if send:
            command = self.__conn.build_luminance(self, lum, time)
            self.__conn.send(command)

    def set_temperature(self, temp, time, send=True):
        """ set colour temperature

        :param temp: colour temperature in kelvin
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if not 'temp' in self.__supported_features:
            return

        temp = max(MIN_TEMPERATURE, temp)
        temp = min(MAX_TEMPERATURE, temp)
        self.__temp = temp

        if send:
            command = self.__conn.build_temp(self, temp, time)
            self.__conn.send(command)

    def set_rgb(self, red, green, blue, time, send=True):
        """ set RGB colour

        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if not 'rgb' in self.__supported_features:
            return

        red = min(red, MAX_COLOUR)
        green = min(green, MAX_COLOUR)
        blue = min(blue, MAX_COLOUR)
        self.__red = red
        self.__green = green
        self.__blue = blue

        if send:
            command = self.__conn.build_colour(self, red, green, blue, time)
            self.__conn.send(command)

    def build_command(self, command, data):
        """ build a light command

        :param command: command id (1 byte)
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        return self.__conn.build_light_command(command, self.__addr, data)

    def __str__(self):
        return '<light %s: %s>' % (self.__addr, self.__name)


class Group:
    """ representation of a group of lights
    """

    def __init__(self, conn, idx, name):
        """
        :param conn: Lightify object
        :param idx: index of the group provided by the gateway
        :param name: group name
        """
        self.__conn = conn
        self.__idx = idx
        self.__name = name
        self.__lights = []

    def name(self):
        """
        :return: name of the group
        """
        return self.__name

    def idx(self):
        """
        :return: index of the group provided by the gateway
        """
        return self.__idx

    def lights(self):
        """
        :return: list of group's light mac addresses
        """
        return self.__lights

    def on(self):
        """
        :return: true if any of the group's lights is on, false otherwise
        """
        return any(self.__conn.lights()[addr].on()
                   for addr in self.__lights if addr in self.__conn.lights())

    def supported_features(self):
        """
        :return: tuple of supported features (on, lum, temp, rgb)
        """
        features = [self.__conn.lights()[addr].supported_features()
                    for addr in self.__lights if addr in self.__conn.lights()]
        features = list(set(sum(features, ())))
        return features

    def lights_attribute(self, attr):
        """ do a best guess about the group's lights attribute

        :param attr: attribute name
        :return: guessed attribute value
        """
        lights = [self.__conn.lights()[addr] for addr in self.__lights
                  if addr in self.__conn.lights()]
        if not lights:
            return 0

        if attr in ('red', 'green', 'blue'):
            feature = 'rgb'
        else:
            feature = attr

        lights = [(feature in light.supported_features(),
                   getattr(light, attr)()) for light in lights]
        lights.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return lights[0][1]

    def lum(self):
        """
        :return: best guess about the group's lights luminance (brightness)
        """
        return self.lights_attribute('lum')

    def temp(self):
        """
        :return: best guess about the group's lights colour temperature in kelvin
        """
        return self.lights_attribute('temp')

    def red(self):
        """
        :return: best guess about the group's lights amount of red
        """
        return self.lights_attribute('red')

    def green(self):
        """
        :return: best guess about the group's lights amount of green
        """
        return self.lights_attribute('green')

    def blue(self):
        """
        :return: best guess about the group's lights amount of blue
        """
        return self.lights_attribute('blue')

    def rgb(self):
        """
        :return: tuple containing (red, green, blue)
        """
        return self.lights_attribute('rgb')

    def set_lights(self, lights):
        """ set group's lights

        :param lights: list of light mac addresses
        :return:
        """
        self.__lights = lights

    def set_onoff(self, on):
        """ set on/off for the group's lights

        :param on: true/false
        :return:
        """
        on = bool(on)
        command = self.__conn.build_onoff(self, on)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_onoff(on, send=False)

    def set_luminance(self, lum, time):
        """ set luminance (brightness) for the group's lights

        :param lum: luminance (brightness)
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return:
        """
        lum = min(MAX_LUMINANCE, lum)
        command = self.__conn.build_luminance(self, lum, time)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_luminance(lum, time, send=False)

    def set_temperature(self, temp, time):
        """ set colour temperature for the group's lights

        :param temp: colour temperature in kelvin
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return:
        """
        temp = max(MIN_TEMPERATURE, temp)
        temp = min(MAX_TEMPERATURE, temp)
        command = self.__conn.build_temp(self, temp, time)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_temperature(temp, time, send=False)

    def set_rgb(self, red, green, blue, time):
        """ set RGB colour for the group's lights

        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return:
        """
        red = min(red, MAX_COLOUR)
        green = min(green, MAX_COLOUR)
        blue = min(blue, MAX_COLOUR)
        command = self.__conn.build_colour(self, red, green, blue, time)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_rgb(red, green, blue, time, send=False)

    def build_command(self, command, data):
        """ build a group command

        :param command: command id (1 byte)
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        return self.__conn.build_command(command, self.__idx, data)

    def __str__(self):
        lights = []
        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                lights.append(str(light))

        return '<group %s: %s, lights: %s>' % (self.__idx, self.__name,
                                               ' '.join(lights))


class Lightify:
    """ main osram lightify class
    """
    def __init__(self, host):
        """
        :param host: lightify gateway host
        """
        self.__logger = logging.getLogger(MODULE)
        self.__logger.addHandler(logging.NullHandler())
        self.__logger.info('Logging %s', MODULE)

        # a sequence number used to number commands sent to the gateway
        self.__seq = 0

        self.__groups = {}
        self.__scenes = {}
        self.__lights = {}
        self.__lights_obtained = False
        self.__groups_obtained = False
        self.__scenes_obtained = False
        self.__lock = threading.RLock()
        self.__host = host
        self.__sock = None
        self.connect()

    def __del__(self):
        try:
            self.__sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        self.__sock.close()

    def connect(self):
        """ establish a connection with the lightify gateway

        :return:
        """
        with self.__lock:
            self.__sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.__sock.settimeout(GATEWAY_TIMEOUT_SECONDS)
            self.__sock.connect((self.__host, PORT))

    def groups(self):
        """
        :return: dict from group name to Group object
        """
        if not self.__lights_obtained:
            self.update_all_light_status()

        if not self.__groups_obtained:
            self.update_group_list()

        return self.__groups

    def scenes(self):
        """
        :return: dict from scene name to Scene object
        """
        if not self.__scenes_obtained:
            self.update_scene_list()

        return self.__scenes

    def lights(self):
        """
        :return: dict from light mac address to Light object
        """
        if not self.__lights_obtained:
            self.update_all_light_status()

        return self.__lights

    def light_byname(self, name):
        """
        :param name: name of the light
        :return: Light object
        """
        if not self.__lights_obtained:
            self.update_all_light_status()

        for light in self.__lights.values():
            if light.name() == name:
                return light

        return None

    def next_seq(self):
        """
        :return: next sequence number
        """
        with self.__lock:
            self.__seq = (self.__seq + 1) % 256
            return self.__seq

    def build_global_command(self, command, data):
        """ build a global command

        :param command: command id (1 byte)
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        return self.build_basic_command(
            FLAG_GLOBAL,
            command,
            '',
            data
        )

    def build_basic_command(self, flag, command, addr, data):
        """ build a basic command

        :param flag: packet type (1 byte)
        :param command: command id (1 byte)
        :param addr: binary device mac address (8 bytes) or empty string
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        length = 6 + len(addr) + len(data)
        if isinstance(addr, str):
            addr = addr.encode('cp437')

        if isinstance(data, str):
            data = data.encode('cp437')

        result = struct.pack(
            '<H6B',
            length,
            flag,
            command,
            0,
            0,
            0x07,
            self.next_seq()
        ) + addr + data

        return result

    def build_command(self, command, idx, data):
        """ build a group or scene command

        :param command: command id (1 byte)
        :param idx: device index
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        # for backward compatibiity
        if isinstance(idx, Group):
            idx = idx.idx()

        return self.build_basic_command(
            FLAG_GLOBAL,
            command,
            struct.pack('<8B', idx, 0, 0, 0, 0, 0, 0, 0),
            data
        )

    def build_light_command(self, command, addr, data):
        """ build a light command

        :param command: command id (1 byte)
        :param addr: light mac address
        :param data: additional binary data
        :return: binary data to be sent to the gateway
        """
        # for backward compatibiity
        if isinstance(addr, Light):
            addr = addr.addr()

        return self.build_basic_command(
            FLAG_LIGHT,
            command,
            struct.pack('<Q', addr),
            data
        )

    @staticmethod
    def build_onoff(item, on):
        """
        :param item: Light or Group object
        :param on: true/false
        :return: binary command to set on/off for the light/group
        """
        return item.build_command(
            COMMAND_ONOFF,
            struct.pack('<B', on)
        )

    @staticmethod
    def build_temp(item, temp, time):
        """
        :param item: Light or Group object
        :param temp: colour temperature in kelvin
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return: binary command to set the light/group colour temperature
        """
        return item.build_command(
            COMMAND_TEMP,
            struct.pack('<HH', temp, time)
        )

    @staticmethod
    def build_luminance(item, lum, time):
        """
        :param item: Light or Group object
        :param lum: luminance (brightness)
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return: binary command to set the light/group luminance (brightness)
        """
        return item.build_command(
            COMMAND_LUMINANCE,
            struct.pack('<BH', lum, time)
        )

    @staticmethod
    def build_colour(item, red, green, blue, time):
        """
        :param item: Light or Group object
        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param time: transition time in 1/10 seconds, 0 to disable transition
        :return: binary command to set the light/group RGB colour
        """
        return item.build_command(
            COMMAND_COLOUR,
            struct.pack('<BBBBH', red, green, blue, DEFAULT_ALPHA, time)
        )

    def build_all_light_status(self, flag=0x01):
        """
        :param flag: additional flag (0x01 to return all available data)
        :return: binary command to get the status of all lights
        """
        return self.build_global_command(
            COMMAND_ALL_LIGHT_STATUS,
            struct.pack('<B', flag)
        )

    @staticmethod
    def build_light_status(light):
        """
        :param light: Light object
        :return: binary command to get the status of the given light
            (only subset of values returned by build_all_light_status)
        """
        return light.build_command(
            COMMAND_LIGHT_STATUS,
            ''
        )

    def build_group_list(self):
        """
        :return: binary command to get the list of groups
        """
        return self.build_global_command(
            COMMAND_GROUP_LIST,
            ''
        )

    def build_scene_list(self):
        """
        :return: binary command to get the list of scenes
        """
        return self.build_global_command(
            COMMAND_SCENE_LIST,
            ''
        )

    def group_list(self):
        """ get the dict of group indices and names
            deprecated, for backward compatibility only!

        :return: dict of groups (key is group index and value is group name)
        """
        self.update_group_list()
        groups = {}
        for name in self.__groups:
            groups[self.__groups[name].idx()] = name

        return groups

    def update_group_list(self):
        """ update all groups

        :return:
        """
        with self.__lock:
            command = self.build_group_list()
            data = self.send(command)
            self.__groups_obtained = True

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of groups: %d', num)
            groups = {}

            for i in range(0, num):
                pos = 9 + i * 18
                payload = data[pos:pos + 18]

                (idx, name) = struct.unpack('<H16s', payload)
                name = name.decode('utf-8').replace('\0', '')

                self.__logger.debug('Group index %d: %s', idx, name)
                group = Group(self, idx, name)
                groups[name] = group

            self.__groups = groups
            self.update_group_lights()

    def update_group_lights(self):
        """ update the list of group's light mac addresses for all groups

        :return:
        """
        for group in self.__groups.values():
            lights = [addr for addr in self.__lights
                      if group.idx() in self.__lights[addr].groups()]
            group.set_lights(lights)

    def group_info(self, group):
        """ get the list of group's light mac addresses
            deprecated, for backward compatibility only!

        :param group: Group object
        :return: list of group's light mac addresses
        """
        self.update_all_light_status()
        return group.lights()

    def update_scene_list(self):
        """ update all scenes

        :return:
        """
        with self.__lock:
            command = self.build_scene_list()
            data = self.send(command)
            self.__scenes_obtained = True

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of scenes: %d', num)
            scenes = {}

            for i in range(0, num):
                pos = 9 + i * 20
                payload = data[pos:pos + 20]

                (idx, name) = struct.unpack('<Bx16s2x', payload)
                name = name.decode('utf-8').replace('\0', '')

                self.__logger.debug('Scene index %d: %s', idx, name)
                scene = Scene(self, idx, name)
                scenes[name] = scene

            self.__scenes = scenes

    def send(self, data, reconnect=True):
        """ send the packet 'data' to the gateway and return the received packet

        :param data: binary command to send
        :param reconnect: if true, will try to reconnect once. if false,
                          will raise a socket.error.
        :return: received packet
        """
        with self.__lock:
            try:
                self.__logger.debug('Sending "%s"', binascii.hexlify(data))
                self.__sock.sendall(data)

                lengthsize = 2
                received_data = self.__sock.recv(lengthsize)
                (length,) = struct.unpack('<H', received_data[:lengthsize])
                self.__logger.debug(
                    'Received "%d %s"',
                    len(received_data),
                    binascii.hexlify(received_data)
                )

                expected = length + 2 - len(received_data)
                self.__logger.debug('Length:   %d', length)
                self.__logger.debug('Expected: %d', expected)
                total_received_data = b''
                while expected > 0:
                    received_data = self.__sock.recv(expected)
                    self.__logger.debug(
                        'Received "%d %s"',
                        len(received_data),
                        binascii.hexlify(received_data)
                    )
                    total_received_data += received_data
                    expected -= len(received_data)
                self.__logger.debug('Received: %s', repr(total_received_data))
            except socket.error as err:
                self.__logger.warning('Lost connection to lightify gateway:')
                self.__logger.warning('socketError: %s', err)
                if reconnect:
                    self.__logger.warning('Trying to reconnect')
                    self.connect()
                    return self.send(data, reconnect=False)

                raise err

            return total_received_data

    def update_light_status(self, light):
        """ get the status of the given light (only subset of values)

        :param light: Light object
        :return: tuple containing (on, lum, temp, red, green, blue)
        """
        with self.__lock:
            command = self.build_light_status(light)
            data = self.send(command)

            unreachable_data_len = 18
            if len(data) == unreachable_data_len:
                return None, None, None, None, None, None

            (on, lum, temp, red, green, blue) = struct.unpack(
                '<19x2BH3B4x', data)

            self.__logger.debug('Light: %x', light.addr())
            self.__logger.debug('onoff: %d', on)
            self.__logger.debug('lum:   %d', lum)
            self.__logger.debug('temp:  %d', temp)
            self.__logger.debug('red:   %d', red)
            self.__logger.debug('green: %d', green)
            self.__logger.debug('blue:  %d', blue)

            return on, lum, temp, red, green, blue

    def update_all_light_status(self):
        """ update the status of all lights

        :return:
        """
        with self.__lock:
            command = self.build_all_light_status()
            data = self.send(command)
            self.__lights_obtained = True

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of lights: %d', num)
            old_lights = self.__lights
            new_lights = {}

            for i in range(0, num):
                pos = 9 + i * 50
                payload = data[pos:pos + 50]
                self.__logger.debug('Light payload: %d %d %d', i, pos,
                                    len(payload))

                try:
                    (addr, stat, name, last_seen) = struct.unpack(
                        '<2xQ16s16sI4x', payload)
                except struct.error as err:
                    self.__logger.warning(
                        'Couldn\'t unpack light status packet:')
                    self.__logger.warning('struct.error: %s', err)
                    self.__logger.warning('payload: %s',
                                          binascii.hexlify(payload))
                    return

                (type_id, ver1_1, ver1_2, ver1_3, ver1_4, reachable, groups,
                 on, lum, temp, red, green, blue) = struct.unpack(
                     '<6BH2BH3Bx', stat)
                if not DeviceSubType.has_value(type_id):
                    self.__logger.warning(
                        'Unknown device type id: %s. Please report to '
                        'https://github.com/tfriedel/python-lightify', type_id)

                name = name.decode('utf-8').replace('\0', '')
                groups = [16 - i for i, val in enumerate(format(groups, '016b'))
                          if val == '1']
                version = '%02d%02d%02d%d' % (ver1_1, ver1_2, ver1_3, ver1_4)

                if addr in old_lights:
                    light = old_lights[addr]
                    self.__logger.debug('Old light: %x', addr)
                else:
                    light = Light(self, addr, type_id)
                    self.__logger.debug('New light: %x', addr)

                self.__logger.debug('name:      %s', name)
                self.__logger.debug('reachable: %d', reachable)
                self.__logger.debug('last seen: %d', last_seen)
                self.__logger.debug('onoff:     %d', on)
                self.__logger.debug('lum:       %d', lum)
                self.__logger.debug('temp:      %d', temp)
                self.__logger.debug('red:       %d', red)
                self.__logger.debug('green:     %d', green)
                self.__logger.debug('blue:      %d', blue)
                self.__logger.debug('type id:   %d', type_id)
                self.__logger.debug('groups:    %s', groups)
                self.__logger.debug('version:   %s', version)

                light.update_status(reachable, last_seen, on, lum, temp, red,
                                    green, blue, name, groups, version)

                new_lights[addr] = light

            for addr in old_lights:
                if not addr in new_lights:
                    old_lights[addr].mark_deleted()

            self.__lights = new_lights
            self.update_group_lights()
