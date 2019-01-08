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
COMMAND_GROUP_INFO = 0x26 # not used as of now
COMMAND_LUMINANCE = 0x31
COMMAND_ONOFF = 0x32
COMMAND_TEMP = 0x33
COMMAND_COLOUR = 0x36
COMMAND_ACTIVATE_SCENE = 0x52
COMMAND_LIGHT_STATUS = 0x68

# Commands
# 13 all light status (returns list of light address, light status, light name)
# 1e group list (returns list of group id, and group name)
# 1f scene list (returns list of scene id, and scene name)
# 26 group status (returns group id, group name, and list of light addresses)
# 31 set group luminance
# 32 set group onoff
# 33 set group temp
# 36 set group colour
# 52 activate scene
# 68 light status (returns light address and light status (?))

LAST_SEEN_DURATION_MINUTES = 5
MAX_TEMPERATURE = 6500
MAX_LUMINANCE = 100
MAX_COLOR = 255
TIMEOUT = 10  # timeout in seconds when communicating with the gateway


class DeviceTypeOriginal(Enum):
    """ original device type as returned by lightify
    """
    LIGHT_NON_SOFTSWITCH = 1
    LIGHT_TUNABLE_WHITE = 2
    LIGHT_FIXED_WHITE = 4
    LIGHT_RGB = 10
    PLUG = 16
    MOTIONSENSOR = 32
    SWITCH_TWO_BUTTONS = 64
    SWITCH_FOUR_BUTTONS = 65

class DeviceType(Enum):
    """ generalized device type
    """
    LIGHT = 1
    PLUG = 2
    MOTIONSENSOR = 3
    SWITCH = 4

ID_TO_DEVICETYPE = defaultdict(lambda: DeviceType.LIGHT)
ID_TO_DEVICETYPE.update({10: DeviceType.LIGHT, 16: DeviceType.PLUG,
                         32: DeviceType.MOTIONSENSOR, 64: DeviceType.SWITCH,
                         65: DeviceType.SWITCH})


class Luminary():
    """ general luminary class
    """
    def __init__(self, conn, logger, name):
        self.__logger = logger
        self.__conn = conn
        self.__name = name

    def name(self):
        """
        :return: the name of the luminary
        """
        return self.__name

    def set_onoff(self, on):
        """ set on/off

        :param on: if true, the luminary is set on, if false it's set off
        :return:
        """
        data = self.__conn.build_onoff(self, on)
        self.__conn.send(data)

    def set_luminance(self, lum, time):
        """ set luminance

        :param lum: luminance or brightness, between 0 and 100. if 0,
                    the luminary is turned off.
        :param time: transition time in 1/10 seconds
        :return:
        """
        lum = min(MAX_LUMINANCE, lum)
        data = self.__conn.build_luminance(self, lum, time)
        self.__conn.send(data)

    def set_temperature(self, temp, time):
        """ set temperature

        :param temp: color temperature in kelvin.
                     typically between 2200 and 6500
        :param time: transition time in 1/10 seconds
        :return:
        """
        temp = min(MAX_TEMPERATURE, temp)
        data = self.__conn.build_temp(self, temp, time)
        self.__conn.send(data)

    def set_rgb(self, r, g, b, time):
        """ set RGB color

        :param r: amount of red. range 0-255
        :param g: amount of green. range 0-255
        :param b: amount of blue. range 0-255
        :param time: transition time in 1/10 seconds
        :return:
        """
        r = min(r, MAX_COLOR)
        g = min(g, MAX_COLOR)
        b = min(b, MAX_COLOR)
        data = self.__conn.build_colour(self, r, g, b, time)
        self.__conn.send(data)


class Light(Luminary):
    """ class for controlling a single light source
    """

    def __init__(self, conn, logger, addr, name):
        super(Light, self).__init__(conn, logger, name)
        self.__logger = logger
        self.__conn = conn
        self.__addr = addr
        self.__reachable = True
        self.__last_seen = 0
        self.__on = False
        self.__lum = 0
        self.__temp = MAX_TEMPERATURE
        self.__r = MAX_COLOR
        self.__g = MAX_COLOR
        self.__b = MAX_COLOR
        self.__devicetype_original = DeviceTypeOriginal.LIGHT_NON_SOFTSWITCH
        self.__devicetype = DeviceType.LIGHT
        self.__groups = []

    def addr(self):
        """
        :return: the mac address of this light source
        """
        return self.__addr

    def __str__(self):
        return "<light: %s>" % self.name()

    def update_status(self, reachable, last_seen, on, lum, temp, r, g, b,
                      devicetype_original, groups):
        """ updates internal representation. does not send out a command
            to the light source!

        :param reachable: if the light is reachable or not
        :param last_seen: time since last seen by gateway
        :param on: if the light is on or off
        :param lum: luminance
        :param temp: color temperature
        :param r: red
        :param g: green
        :param b: blue
        :param devicetype_original: original device type
        :param groups: list of associated group ids
        :return:
        """
        devicetype_original = DeviceTypeOriginal(devicetype_original)
        devicetype = ID_TO_DEVICETYPE[devicetype_original]
        last_seen = last_seen * LAST_SEEN_DURATION_MINUTES
        reachable = bool(reachable)
        on = bool(on)
        if not reachable:
            on = False

        if devicetype_original in (DeviceTypeOriginal.MOTIONSENSOR,
                                   DeviceTypeOriginal.SWITCH_TWO_BUTTONS,
                                   DeviceTypeOriginal.SWITCH_FOUR_BUTTONS):
            on = False
            lum = 0
            temp = 0
            r = 0
            g = 0
            b = 0
        elif devicetype_original == DeviceTypeOriginal.PLUG:
            lum = MAX_LUMINANCE
            temp = MAX_TEMPERATURE
            r = MAX_COLOR
            g = MAX_COLOR
            b = MAX_COLOR
        elif devicetype_original in (DeviceTypeOriginal.LIGHT_NON_SOFTSWITCH,
                                     DeviceTypeOriginal.LIGHT_TUNABLE_WHITE,
                                     DeviceTypeOriginal.LIGHT_FIXED_WHITE):
            r = MAX_COLOR
            g = MAX_COLOR
            b = MAX_COLOR

        self.__groups = groups
        self.__devicetype_original = devicetype_original
        self.__devicetype = devicetype
        self.__reachable = reachable
        self.__last_seen = last_seen
        self.__on = on
        self.__lum = lum
        self.__temp = temp
        self.__r = r
        self.__g = g
        self.__b = b

    def reachable(self):
        """
        :return: true if the light is reachable, false otherwise
        """
        return self.__reachable

    def last_seen(self):
        """
        :return: time since last seen by gateway (minutes)
        """
        return self.__last_seen

    def on(self):
        """
        :return: true if the status of the light is on, false otherwise
        """
        return self.__on

    def set_onoff(self, on):
        """ set on/off

        :param on: if true, sends a command to turn on the light, updates state
                   of on and luminance variables
        :return:
        """
        on = bool(on)
        self.__on = on
        super(Light, self).set_onoff(on)
        if self.lum() == 0 and on:
            self.__lum = 1  # This seems to be the default

    def lum(self):
        """
        :return: the luminance
        """
        return self.__lum

    def set_luminance(self, lum, time):
        """ set luminance

        :param lum: luminance or brightness, between 0 and 100. if 0,
                    the luminary is turned off.
        :param time: transition time in 1/10 seconds
        :return:
        """
        self.__lum = min(MAX_LUMINANCE, lum)
        super(Light, self).set_luminance(lum, time)
        if lum > 0 and not self.__on:
            self.__on = True
        elif lum == 0 and self.__on:
            self.__on = False

    def temp(self):
        """
        :return: the color temperature in kelvin
        """
        return self.__temp

    def set_temperature(self, temp, time):
        """ set temperature

        :param temp: color temperature in kelvin.
                     typically between 2200 and 6500
        :param time: transition time in 1/10 seconds
        :return:
        """
        self.__temp = min(MAX_TEMPERATURE, temp)
        super(Light, self).set_temperature(temp, time)

    def rgb(self):
        """
        :return: a tuple containing (red, green, blue).
                 with values between 0 and 255
        """
        return self.red(), self.green(), self.blue()

    def set_rgb(self, r, g, b, time):
        """ set RGB color

        :param r: amount of red. range 0-255
        :param g: amount of green. range 0-255
        :param b: amount of blue. range 0-255
        :param time: transition time in 1/10 seconds
        :return:
        """
        self.__r = min(r, MAX_COLOR)
        self.__g = min(g, MAX_COLOR)
        self.__b = min(b, MAX_COLOR)

        super(Light, self).set_rgb(r, g, b, time)

    def red(self):
        """
        :return: amount of red. range 0-255
        """
        return self.__r

    def green(self):
        """
        :return: amount of green. range 0-255
        """
        return self.__g

    def blue(self):
        """
        :return: amount of blue. range 0-255
        """
        return self.__b

    def build_command(self, command, data):
        """ build a command

        :param command: binary command (one byte)
        :param data: packed binary data
        :return:
        """
        if isinstance(data, str):
            data = data.encode('cp437')

        return self.__conn.build_light_command(command, self, data)

    def devicetype(self):
        """
        :return: generalized device type
        """
        return self.__devicetype

    def devicetype_original(self):
        """
        :return: original device type as returned by lightify
        """
        return self.__devicetype_original

    def groups(self):
        """
        :return: list of associated group ids
        """
        return self.__groups


class Group(Luminary):
    """ representation of a group of lights
    """

    def __init__(self, conn, logger, idx, name):
        super(Group, self).__init__(conn, logger, name)
        self.__conn = conn
        self.__logger = logger
        self.__idx = idx
        self.__lights = []

    def idx(self):
        """
        :return: the index of the light group provided by the gateway
        """
        return self.__idx

    def lights(self):
        """
        :return: list of light mac addresses of lights in this group
        """
        return self.__lights

    def set_lights(self, lights):
        """ set group's lights

        :param lights: set the group to contain this list of light mac addresses
        :return:
        """
        self.__lights = lights

    def on(self):
        """
        :return: true if any of the group's lights is on, false otherwise
        """
        return any([self.__conn.lights()[light].on() for light in self.lights()])

    def __str__(self):
        lights_str = ""
        for light_addr in self.lights():
            if light_addr in self.__conn.lights():
                light = self.__conn.lights()[light_addr]
            else:
                light = "%x" % light_addr

            lights_str = lights_str + str(light) + " "

        return "<group: %s, lights: %s>" % (self.name(), lights_str)

    def build_command(self, command, data):
        """ build a command

        :param command: binary command (one byte)
        :param data: packed binary data
        :return:
        """
        if isinstance(data, str):
            data = data.encode('cp437')

        return self.__conn.build_command(command, self, data)


class Scene:
    """ representation of a scene
    """
    def __init__(self, conn, logger, idx, name):
        self.__logger = logger
        self.__conn = conn
        self.__name = name
        self.__idx = idx

    def name(self):
        """
        :return: the scene name
        """
        return self.__name

    def idx(self):
        """
        :return: the index of the scene provided by the gateway
        """
        return self.__idx

    def activate(self):
        """ activate the scene

        :return:
        """
        data = self.__conn.build_command(COMMAND_ACTIVATE_SCENE, self, '')
        self.__conn.send(data)

    def __str__(self):
        return '<scene: %s>' % (self.name(),)


class Lightify:
    """ main osram lightify class
    """
    def __init__(self, host):
        self.__logger = logging.getLogger(MODULE)
        self.__logger.addHandler(logging.NullHandler())
        self.__logger.info("Logging %s", MODULE)

        # a sequence number which is used to number commands sent to the gateway
        self.__seq = 1

        self.__groups = {}
        self.__scenes = {}
        self.__lights = {}
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
        """ establish a connection with the lightify gateway.
        """
        with self.__lock:
            self.__sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.__sock.settimeout(TIMEOUT)
            self.__sock.connect((self.__host, PORT))

    def groups(self):
        """ dict from group name to Group object.
        """
        return self.__groups

    def scenes(self):
        """ dict from scene name to Scene object.
        """
        return self.__scenes

    def lights(self):
        """ dict from light addr to Light object.
        """
        return self.__lights

    def light_byname(self, name):
        self.__logger.debug(len(self.lights()))

        for light in self.lights().values():
            if light.name() == name:
                return light

        return None

    def next_seq(self):
        with self.__lock:
            self.__seq = (self.__seq + 1) % 256
            return self.__seq

    def build_global_command(self, command, data):
        length = 6 + len(data)
        if isinstance(data, str):
            data = data.encode('cp437')

        result = struct.pack(
            "<H6B",
            length,
            0x02,
            command,
            0,
            0,
            0x7,
            self.next_seq()
        ) + data

        return result

    def build_basic_command(self, flag, command, item, data):
        length = 14 + len(data)
        if isinstance(data, str):
            data = data.encode('cp437')

        if isinstance(item, str):
            item = item.decode('cp437')

        result = struct.pack(
            "<H6B",
            length,
            flag,
            command,
            0,
            0,
            0x7,
            self.next_seq()
        ) + item + data

        return result

    def build_command(self, command, item, data):
        return self.build_basic_command(
            0x02,
            command,
            struct.pack("<8B", item.idx(), 0, 0, 0, 0, 0, 0, 0),
            data)

    def build_light_command(self, command, light, data):
        return self.build_basic_command(
            0x00,
            command,
            struct.pack("<Q", light.addr()),
            data
        )

    @staticmethod
    def build_onoff(item, on):
        return item.build_command(COMMAND_ONOFF, struct.pack("<B", on))

    @staticmethod
    def build_temp(item, temp, time):
        return item.build_command(COMMAND_TEMP, struct.pack("<HH", temp, time))

    @staticmethod
    def build_luminance(item, luminance, time):
        return item.build_command(
            COMMAND_LUMINANCE,
            struct.pack("<BH", luminance, time)
        )

    @staticmethod
    def build_colour(item, red, green, blue, time):
        return item.build_command(
            COMMAND_COLOUR,
            struct.pack("<BBBBH", red, green, blue, 0xff, time)
        )

    def build_all_light_status(self, flag):
        return self.build_global_command(
            COMMAND_ALL_LIGHT_STATUS,
            struct.pack("<B", flag)
        )

    @staticmethod
    def build_light_status(light):
        return light.build_command(COMMAND_LIGHT_STATUS, "".encode('cp437'))

    def build_group_list(self):
        return self.build_global_command(COMMAND_GROUP_LIST, "".encode('cp437'))

    def group_list(self):
        with self.__lock:
            groups = {}
            data = self.build_group_list()
            data = self.send(data)
            (num,) = struct.unpack("<H", data[7:9])
            self.__logger.debug('Num %d', num)

            for i in range(0, num):
                pos = 9 + i * 18
                payload = data[pos:pos + 18]

                (idx, name) = struct.unpack("<H16s", payload)
                name = name.decode('utf-8').replace('\0', "")

                groups[idx] = name
                self.__logger.debug("Idx %d: '%s'", idx, name)

            return groups

    def update_group_list(self):
        with self.__lock:
            lst = self.group_list()
            groups = {}

            for (idx, name) in lst.items():
                group = Group(self, self.__logger, idx, name)
                groups[name] = group

            self.__groups = groups
            self.update_group_lights()

    def update_group_lights(self):
        for group in self.groups().values():
            lights = [addr for addr in self.lights()
                      if group.idx() in self.lights()[addr].groups()]
            group.set_lights(lights)

    def build_scene_list(self):
        return self.build_global_command(COMMAND_SCENE_LIST, "".encode('cp437'))

    def scene_list(self):
        with self.__lock:
            scenes = {}
            data = self.build_scene_list()
            data = self.send(data)
            (num,) = struct.unpack("<H", data[7:9])
            self.__logger.debug('Num %d', num)

            for i in range(0, num):
                pos = 9 + i * 20
                payload = data[pos:pos + 20]

                (idx, name) = struct.unpack("<Bx16s2x", payload)
                name = name.decode('utf-8').replace('\0', '')

                scenes[idx] = name
                self.__logger.debug("Idx %d: '%s'", idx, name)

            return scenes

    def update_scene_list(self):
        with self.__lock:
            lst = self.scene_list()
            scenes = {}

            for (idx, name) in lst.items():
                scene = Scene(self, self.__logger, idx, name)
                scenes[name] = scene

            self.__scenes = scenes

    def send(self, data, reconnect=True):
        """  sends the packet 'data' to the gateway and returns the
             received packet.

        :param data: a string containing binary data
        :param reconnect: if true, will try to reconnect once. if false,
                          will raise an socket.error
        :return: received packet
        """
        with self.__lock:
            try:
                # send
                self.__logger.debug('sending "%s"', binascii.hexlify(data))
                self.__sock.sendall(data)

                # receive
                lengthsize = 2
                received_data = self.__sock.recv(lengthsize)
                (length,) = struct.unpack("<H", received_data[:lengthsize])

                self.__logger.debug(len(received_data))
                expected = length + 2 - len(received_data)
                self.__logger.debug("Length %d", length)
                self.__logger.debug("Expected %d", expected)
                total_received_data = b''
                while expected > 0:
                    self.__logger.debug(
                        'received "%d %s"',
                        length,
                        binascii.hexlify(received_data)
                    )
                    received_data = self.__sock.recv(expected)
                    total_received_data += received_data
                    expected -= len(received_data)
                self.__logger.debug('received %s', repr(total_received_data))
            except socket.error as err:
                self.__logger.warning('lost connection to lightify gateway.')
                self.__logger.warning('socketError: %s', err)
                if reconnect:
                    self.__logger.warning('Trying to reconnect.')
                    self.connect()
                    return self.send(data, reconnect=False)

                raise err
            return total_received_data

    def update_light_status(self, light):
        with self.__lock:
            data = self.build_light_status(light)
            data = self.send(data)

            unreachable_data_len = 18
            if len(data) == unreachable_data_len:
                (on, lum, temp, r, g, b) = (False, None, None, None, None, None)
                return on, lum, temp, r, g, b

            (on, lum, temp, r, g, b) = struct.unpack("<19x2BH3B4x", data)

            self.__logger.debug('onoff: %d', on)
            self.__logger.debug('temp:  %d', temp)
            self.__logger.debug('lum:   %d', lum)
            self.__logger.debug('red:   %d', r)
            self.__logger.debug('green: %d', g)
            self.__logger.debug('blue:  %d', b)

            return on, lum, temp, r, g, b

    def update_all_light_status(self):
        with self.__lock:
            data = self.build_all_light_status(1)
            data = self.send(data)
            (num,) = struct.unpack("<H", data[7:9])
            self.__logger.debug('num: %d', num)

            old_lights = self.__lights
            new_lights = {}

            for i in range(0, num):
                pos = 9 + i * 50
                payload = data[pos:pos + 50]
                self.__logger.debug("%d %d %d", i, pos, len(payload))

                try:
                    (addr, stat, name, last_seen) = struct.unpack(
                        "<2xQ16s16sI4x", payload)
                except struct.error as err:
                    self.__logger.warning(
                        "couldn't unpack light status packet.")
                    self.__logger.warning("struct.error: %s", err)
                    self.__logger.warning("payload: %s",
                                          binascii.hexlify(payload))
                    return

                try:
                    name = name.replace('\0', "")
                except TypeError:
                    # Names are UTF-8 encoded, but not data.
                    name = name.decode('utf-8').replace('\0', "")

                self.__logger.debug('light: %x %s', addr, name)

                if addr in old_lights:
                    light = old_lights[addr]
                else:
                    light = Light(self, self.__logger, addr, name)

                (devicetype, ver1_1, ver1_2, ver1_3, ver1_4, reachable, groups,
                 on, lum, temp, r, g, b) = struct.unpack("<6BH2BH3Bx", stat)
                groups = [16 - i for i, val in enumerate(format(groups, '016b'))
                          if val == '1']
                version_string = "%02d%02d%02d%d" % (ver1_1, ver1_2, ver1_3,
                                                     ver1_4)

                self.__logger.debug('groups: %s', groups)
                self.__logger.debug('reachable: %d', reachable)
                self.__logger.debug('onoff: %d', on)
                self.__logger.debug('temp:  %d', temp)
                self.__logger.debug('lum:   %d', lum)
                self.__logger.debug('red:   %d', r)
                self.__logger.debug('green: %d', g)
                self.__logger.debug('blue:  %d', b)
                self.__logger.debug('last seen: %d', last_seen)
                self.__logger.debug('version: %s', version_string)

                light.update_status(reachable, last_seen, on, lum, temp, r, g, b,
                                    devicetype, groups)
                new_lights[addr] = light

            self.__lights = new_lights
            self.update_group_lights()
