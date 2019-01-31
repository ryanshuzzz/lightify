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
# TODO: Support for sensors
#

import binascii
import hashlib
import logging
import socket
import struct
import threading
import time
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

DEFAULT_ALPHA = 0xff
FLAG_LIGHT = 0x00
FLAG_GLOBAL = 0x02
LAST_SEEN_DURATION_MINUTES = 5
NO_RGB_VALUES = (1, 0, 0)
TYPE_LIGHT_TUNABLE_WHITE = 2
TYPE_LIGHT_RGB = 10

DEFAULT_LUMINANCE = 1
DEFAULT_TEMPERATURE = 2700
MIN_TEMPERATURE_TUNABLE_WHITE = 2700
MAX_TEMPERATURE_TUNABLE_WHITE = 6500
MIN_TEMPERATURE_RGB = 1900
MAX_TEMPERATURE_RGB = 6500
MAX_LUMINANCE = 100
MAX_COLOUR = 255

GATEWAY_TIMEOUT_SECONDS = 10
OUTDATED_TIMESTAMP = 1
UNKNOWN_DEVICENAME = 'unknown device'


class DeviceSubType(Enum):
    """ device sub type
    """
    LIGHT_FIXED_WHITE = 1
    LIGHT_TUNABLE_WHITE = 2
    LIGHT_RGB = 3
    PLUG = 4
    CONTACT_SENSOR = 5
    MOTION_SENSOR = 6
    SWITCH = 7


class DeviceType(Enum):
    """ generalized device type
    """
    LIGHT = 1
    PLUG = 2
    SENSOR = 3
    SWITCH = 4


DEVICE_TYPES = {
    1: {'type': DeviceType.LIGHT,
        'subtype': DeviceSubType.LIGHT_FIXED_WHITE,
        'name': 'light non softswitch'},
    2: {'type': DeviceType.LIGHT,
        'subtype': DeviceSubType.LIGHT_TUNABLE_WHITE,
        'name': 'light tunable white'},
    4: {'type': DeviceType.LIGHT,
        'subtype': DeviceSubType.LIGHT_FIXED_WHITE,
        'name': 'light fixed white'},
    10: {'type': DeviceType.LIGHT,
         'subtype': DeviceSubType.LIGHT_RGB,
         'name': 'light rgb'},
    16: {'type': DeviceType.PLUG,
         'subtype': DeviceSubType.PLUG,
         'name': 'plug'},
    31: {'type': DeviceType.SENSOR,
         'subtype': DeviceSubType.CONTACT_SENSOR,
         'name': 'contact sensor'},
    32: {'type': DeviceType.SENSOR,
         'subtype': DeviceSubType.MOTION_SENSOR,
         'name': 'motion sensor'},
    64: {'type': DeviceType.SWITCH,
         'subtype': DeviceSubType.SWITCH,
         'name': '2 button switch'},
    65: {'type': DeviceType.SWITCH,
         'subtype': DeviceSubType.SWITCH,
         'name': '4 button switch'},
    66: {'type': DeviceType.SWITCH,
         'subtype': DeviceSubType.SWITCH,
         'name': '3 button switch'},
    67: {'type': DeviceType.SWITCH,
         'subtype': DeviceSubType.SWITCH,
         'name': 'unknown switch'},
    68: {'type': DeviceType.SWITCH,
         'subtype': DeviceSubType.SWITCH,
         'name': 'unknown switch'}
}


class Scene:
    """ representation of a scene
    """
    def __init__(self, conn, idx, name, group):
        """
        :param conn: Lightify object
        :param idx: index of the scene provided by the gateway
        :param group: associated group index
        :param name: scene name
        """
        self.__conn = conn
        self.__idx = idx
        self.__name = name
        self.__group = group
        self.__deleted = False

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

    def group(self):
        """
        :return: associated group index
        """
        return self.__group

    def deleted(self):
        """
        :return: whether the scene is deleted from gateway or not
        """
        return self.__deleted

    def mark_deleted(self):
        """ mark the scene as deleted from gateway
        """
        self.__deleted = True

    def activate(self):
        """ activate the scene

        :return:
        """
        if self.__deleted:
            return

        command = self.__conn.build_command(COMMAND_ACTIVATE_SCENE, self.__idx,
                                            '')
        self.__conn.send(command)
        self.__conn.set_lights_updated()

    def __str__(self):
        return '<scene %s: %s, group: %s>' % (self.__idx, self.__name,
                                              self.__group)


class Light:
    """ class for controlling a single light source
    """

    def __init__(self, conn, addr, type_id, type_id_assumed):
        """
        :param conn: Lightify object
        :param addr: mac address of the light
        :param type_id: original device type id as returned by gateway
        :param type_id_assumed: assumed device type id (if type belongs to an
            unknown device)
        """
        self.__conn = conn
        self.__addr = addr
        self.__name = ''
        self.__reachable = True
        self.__last_seen = 0
        self.__onoff = False
        self.__groups = []
        self.__version = ''
        self.__deleted = False
        self.__type_id = type_id
        self.__idx = 0

        device_info = conn.device_types()[type_id_assumed]
        self.__devicesubtype = device_info['subtype']
        self.__devicetype = device_info['type']
        if type_id == type_id_assumed:
            self.__devicename = device_info['name']
        else:
            self.__devicename = UNKNOWN_DEVICENAME

        if self.__devicesubtype in (DeviceSubType.CONTACT_SENSOR,
                                    DeviceSubType.MOTION_SENSOR,
                                    DeviceSubType.SWITCH):
            self.__lum = 0
            self.__temp = 0
            self.__red = 0
            self.__green = 0
            self.__blue = 0
            self.__supported_features = set()
            self.__min_temp = self.__temp
            self.__max_temp = self.__temp
        else:
            self.__lum = MAX_LUMINANCE
            self.__temp = DEFAULT_TEMPERATURE
            self.__red = MAX_COLOUR
            self.__green = MAX_COLOUR
            self.__blue = MAX_COLOUR

            if self.__devicesubtype == DeviceSubType.PLUG:
                self.__supported_features = set(('on',))
                self.__min_temp = self.__temp
                self.__max_temp = self.__temp
            elif self.__devicesubtype == DeviceSubType.LIGHT_FIXED_WHITE:
                self.__supported_features = set(('on', 'lum'))
                self.__min_temp = self.__temp
                self.__max_temp = self.__temp
            elif self.__devicesubtype == DeviceSubType.LIGHT_TUNABLE_WHITE:
                self.__supported_features = set(('on', 'lum', 'temp'))
                self.__min_temp = device_info.get(
                    'min_temp', MIN_TEMPERATURE_TUNABLE_WHITE)
                self.__max_temp = device_info.get(
                    'max_temp', MAX_TEMPERATURE_TUNABLE_WHITE)
            else:
                self.__supported_features = set(('on', 'lum', 'temp', 'rgb'))
                self.__min_temp = device_info.get('min_temp',
                                                  MIN_TEMPERATURE_RGB)
                self.__max_temp = device_info.get('max_temp',
                                                  MAX_TEMPERATURE_RGB)

    def name(self):
        """
        :return: name of the light
        """
        return self.__name

    def idx(self):
        """
        :return: index of the light provided by the gateway
        """
        return self.__idx

    def addr(self):
        """
        :return: mac address of the light
        """
        return self.__addr

    def reachable(self):
        """
        :return: true if the light is reachable
        """
        return self.__reachable

    def last_seen(self):
        """
        :return: time since last seen by gateway in minutes
        """
        return self.__last_seen

    def on(self):
        """
        :return: true if the status of the light is on
        """
        return self.__onoff

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

    def min_temp(self):
        """
        :return: minimum supported colour temperature in kelvin
        """
        if 'temp' in self.__supported_features:
            return self.__min_temp

        return self.__temp

    def max_temp(self):
        """
        :return: maximum supported colour temperature in kelvin
        """
        if 'temp' in self.__supported_features:
            return self.__max_temp

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

    def devicename(self):
        """
        :return: device name
        """
        return self.__devicename

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
        :return: set of supported features (on, lum, temp, rgb)
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

    def update_status(self, reachable, last_seen, onoff, lum, temp, red, green,
                      blue, name, groups, version, idx):
        """ update internal representation
            does not send out a command to the light source!

        :param reachable: whether the light is reachable or not
        :param last_seen: time since last seen by gateway
        :param onoff: whether the light is on or off
        :param lum: luminance (brightness)
        :param temp: colour temperature
        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param name: name of the light
        :param groups: list of associated group indices
        :param version: firmware version
        :param idx: index of the light provided by the gateway
        :return:
        """
        self.__reachable = bool(reachable)
        self.__last_seen = last_seen * LAST_SEEN_DURATION_MINUTES
        self.__name = name
        self.__groups = groups
        self.__version = version
        self.__idx = idx

        if 'on' in self.__supported_features:
            self.__onoff = bool(onoff)

        if 'lum' in self.__supported_features:
            self.__lum = lum

        if 'temp' in self.__supported_features:
            self.__temp = temp

        if 'rgb' in self.__supported_features:
            self.__red = red
            self.__green = green
            self.__blue = blue

    def set_onoff(self, onoff, send=True):
        """ set on/off

        :param onoff: true/false
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if 'on' not in self.__supported_features:
            return

        onoff = bool(onoff)
        self.__onoff = onoff
        if onoff and self.__lum == 0:
            self.__lum = DEFAULT_LUMINANCE

        if send:
            command = self.__conn.build_onoff(self, onoff)
            self.__conn.send(command)
            self.__conn.set_lights_changed()

    def set_luminance(self, lum, transition, send=True):
        """ set luminance (brightness)

        :param lum: luminance (brightness). if 0, the light is turned off.
        :param transition: transition time in 1/10 seconds, 0 to disable
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if 'lum' not in self.__supported_features:
            return

        lum = min(int(lum), MAX_LUMINANCE)
        self.__lum = lum
        if lum > 0:
            self.__lum = lum
            self.__onoff = True
        elif lum == 0:
            self.__lum = DEFAULT_LUMINANCE
            self.__onoff = False

        if send:
            command = self.__conn.build_luminance(self, lum, transition)
            self.__conn.send(command)
            self.__conn.set_lights_changed()

    def set_temperature(self, temp, transition, send=True):
        """ set colour temperature

        :param temp: colour temperature in kelvin
        :param transition: transition time in 1/10 seconds, 0 to disable
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if 'temp' not in self.__supported_features:
            return

        temp = max(self.min_temp(), int(temp))
        temp = min(temp, self.max_temp())
        self.__temp = temp

        if send:
            command = self.__conn.build_temp(self, temp, transition)
            self.__conn.send(command)
            self.__conn.set_lights_changed()

    def set_rgb(self, red, green, blue, transition, send=True):
        """ set RGB colour

        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param transition: transition time in 1/10 seconds, 0 to disable
        :param send: whether to send a command to gateway
        :return:
        """
        if self.__deleted:
            return

        if 'rgb' not in self.__supported_features:
            return

        red = min(int(red), MAX_COLOUR)
        green = min(int(green), MAX_COLOUR)
        blue = min(int(blue), MAX_COLOUR)
        self.__red = red
        self.__green = green
        self.__blue = blue

        if send:
            command = self.__conn.build_colour(self, red, green, blue,
                                               transition)
            self.__conn.send(command)
            self.__conn.set_lights_changed()

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
        self.__scenes = []
        self.__supported_features = set()
        self.__min_temp = 0
        self.__max_temp = 0
        self.__deleted = False

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

    def light_names(self):
        """
        :return: list of group's light names
        """
        return [self.__conn.lights()[addr].name() for addr in self.__lights]

    def scenes(self):
        """
        :return: list of group's scene names
        """
        return self.__scenes

    def update_status(self):
        """ update internal representation

        :return:
        """
        features = [self.__conn.lights()[addr].supported_features()
                    for addr in self.__lights if addr in self.__conn.lights()]
        self.__supported_features = set.union(*features)
        self.__min_temp = min(self.__conn.lights()[addr].min_temp()
                              for addr in self.__lights
                              if addr in self.__conn.lights())
        self.__max_temp = max(self.__conn.lights()[addr].max_temp()
                              for addr in self.__lights
                              if addr in self.__conn.lights())

    def supported_features(self):
        """
        :return: set of supported features (on, lum, temp, rgb)
        """
        return self.__supported_features

    def min_temp(self):
        """
        :return: minimum supported colour temperature of the group's lights
                 in kelvin
        """
        return self.__min_temp

    def max_temp(self):
        """
        :return: maximum supported colour temperature of the group's lights
                 in kelvin
        """
        return self.__max_temp

    def on(self):
        """
        :return: true if any of the group's lights is on
        """
        return any(self.__conn.lights()[addr].on()
                   for addr in self.__lights if addr in self.__conn.lights())

    def reachable(self):
        """
        :return: true if any of the group's lights is reachable
        """
        return any(self.__conn.lights()[addr].reachable()
                   for addr in self.__lights if addr in self.__conn.lights())

    def _lights_attribute(self, attr, feature):
        """ do a best guess about the group's lights attribute

        :param attr: attribute name
        :param feature: supported feature for ordering
        :return: guessed attribute value
        """
        lights = [self.__conn.lights()[addr] for addr in self.__lights
                  if addr in self.__conn.lights()]
        if not lights:
            return 0

        lights = [(feature in light.supported_features(),
                   getattr(light, attr)()) for light in lights]
        lights.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return lights[0][1]

    def lum(self):
        """
        :return: best guess about the group's lights luminance (brightness)
        """
        return self._lights_attribute('lum', 'lum')

    def temp(self):
        """
        :return: best guess about the group's lights colour temperature in
                 kelvin
        """
        return self._lights_attribute('temp', 'temp')

    def red(self):
        """
        :return: best guess about the group's lights amount of red
        """
        return self._lights_attribute('red', 'rgb')

    def green(self):
        """
        :return: best guess about the group's lights amount of green
        """
        return self._lights_attribute('green', 'rgb')

    def blue(self):
        """
        :return: best guess about the group's lights amount of blue
        """
        return self._lights_attribute('blue', 'rgb')

    def rgb(self):
        """
        :return: tuple containing (red, green, blue)
        """
        return self._lights_attribute('rgb', 'rgb')

    def set_lights(self, lights):
        """ set group's lights

        :param lights: list of light mac addresses
        :return:
        """
        self.__lights = lights

    def set_scenes(self, scenes):
        """ set group's scenes

        :param scenes: list of group's scene names
        :return:
        """
        self.__scenes = scenes

    def deleted(self):
        """
        :return: whether the group is deleted from gateway or not
        """
        return self.__deleted

    def mark_deleted(self):
        """ mark the group as deleted from gateway
        """
        self.__deleted = True

    def set_onoff(self, onoff):
        """ set on/off for the group's lights

        :param onoff: true/false
        :return:
        """
        if self.__deleted:
            return

        onoff = bool(onoff)
        command = self.__conn.build_onoff(self, onoff)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_onoff(onoff, send=False)

        self.__conn.set_lights_changed()

    def set_luminance(self, lum, transition):
        """ set luminance (brightness) for the group's lights

        :param lum: luminance (brightness)
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return:
        """
        if self.__deleted:
            return

        lum = min(int(lum), MAX_LUMINANCE)
        command = self.__conn.build_luminance(self, lum, transition)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_luminance(lum, transition, send=False)

        self.__conn.set_lights_changed()

    def set_temperature(self, temp, transition):
        """ set colour temperature for the group's lights

        :param temp: colour temperature in kelvin
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return:
        """
        if self.__deleted:
            return

        temp = max(self.min_temp(), int(temp))
        temp = min(temp, self.max_temp())
        command = self.__conn.build_temp(self, temp, transition)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_temperature(temp, transition, send=False)

        self.__conn.set_lights_changed()

    def set_rgb(self, red, green, blue, transition):
        """ set RGB colour for the group's lights

        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return:
        """
        if self.__deleted:
            return

        red = min(int(red), MAX_COLOUR)
        green = min(int(green), MAX_COLOUR)
        blue = min(int(blue), MAX_COLOUR)
        command = self.__conn.build_colour(self, red, green, blue, transition)
        self.__conn.send(command)

        for addr in self.__lights:
            if addr in self.__conn.lights():
                light = self.__conn.lights()[addr]
                light.set_rgb(red, green, blue, transition, send=False)

        self.__conn.set_lights_changed()

    def activate_scene(self, name):
        """ activate a group's scene

        :param name: scene name
        :return:
        """
        if name in self.__scenes:
            scene = self.__conn.scenes().get(name)
            if scene:
                scene.activate()

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
    def __init__(self, host, new_device_types=None):
        """
        :param host: lightify gateway host
        :param new_device_types: dict of additional device types to merge with
            default device types:
            {<type_id>: {
                'type': <DeviceType instance>,
                'subtype': <DeviceSubType instance>,
                'name': <name of the device>,
                # only for LIGHT_TUNABLE_WHITE and LIGHT_RGB:
                'min_temp': <minimum temperature>, # optional, default:
                                                   # 2700 (LIGHT_TUNABLE_WHITE)
                                                   # 1900 (LIGHT_RGB)
                'max_temp': <maximum temperature>  # optional, default: 6500
             },
             ...
            }
            For example:
            {128: {
                'type': DeviceType.LIGHT,
                'subtype': DeviceSubType.LIGHT_TUNABLE_WHITE,
                'name': 'tradfri tunable white',
                'min_temp': 2700,
                'max_temp': 6500
             }
            }
        }}
        """
        self.__device_types = DEVICE_TYPES.copy()
        self.__device_types.update(new_device_types or {})

        self.__logger = logging.getLogger(MODULE)
        self.__logger.addHandler(logging.NullHandler())
        self.__logger.info('Logging %s', MODULE)

        # a sequence number used to number commands sent to the gateway
        self.__seq = 0

        self.__groups = {}
        self.__scenes = {}
        self.__lights = {}
        self.__groups_updated = 0
        self.__scenes_updated = 0
        self.__lights_updated = 0
        self.__lights_changed = 0
        self.__lights_hash = ''
        self.__lock = threading.RLock()
        self.__host = host
        self.__sock = None
        self._connect()

    def __del__(self):
        try:
            self.__sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        self.__sock.close()

    def _connect(self):
        """ establish a connection with the lightify gateway

        :return:
        """
        with self.__lock:
            self.__sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.__sock.settimeout(GATEWAY_TIMEOUT_SECONDS)
            self.__sock.connect((self.__host, PORT))

    def _next_seq(self):
        """
        :return: next sequence number
        """
        with self.__lock:
            self.__seq = (self.__seq + 1) % 256
            return self.__seq

    def set_lights_updated(self):
        """ update lights updated timestamp

        :return:
        """
        self.__lights_updated = OUTDATED_TIMESTAMP

    def set_lights_changed(self):
        """ update lights hash and changed timestamp

        :return:
        """
        self.__lights_hash = ''
        self.__lights_changed = time.time()

    def groups_updated(self):
        """
        :return: timestamp when the groups were updated last time
        """
        return self.__groups_updated

    def scenes_updated(self):
        """
        :return: timestamp when the scenes were updated last time
        """
        return self.__scenes_updated

    def lights_updated(self):
        """
        :return: timestamp when the lights were updated last time
        """
        return self.__lights_updated

    def lights_changed(self):
        """
        :return: timestamp when the lights values were changed last time
        """
        return self.__lights_changed

    def groups(self):
        """
        :return: dict from group name to Group object
        """
        if not self.__lights_updated:
            self.update_all_light_status()

        if not self.__scenes_updated:
            self.update_scene_list()

        if not self.__groups_updated:
            self.update_group_list()

        return self.__groups

    def device_types(self):
        """
        :return: dict with device types information
        """
        return self.__device_types

    def scenes(self):
        """
        :return: dict from scene name to Scene object
        """
        if not self.__scenes_updated:
            self.update_scene_list()

        return self.__scenes

    def lights(self):
        """
        :return: dict from light mac address to Light object
        """
        if not self.__lights_updated:
            self.update_all_light_status()

        return self.__lights

    def light_byname(self, name):
        """
        :param name: name of the light
        :return: Light object
        """
        if not self.__lights_updated:
            self.update_all_light_status()

        for light in self.__lights.values():
            if light.name() == name:
                return light

        return None

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
            # keep compatibiity with Python 2.7
            try:
                addr = addr.encode('cp437')
            except UnicodeDecodeError:
                pass

        if isinstance(data, str):
            # keep compatibiity with Python 2.7
            try:
                data = data.encode('cp437')
            except UnicodeDecodeError:
                pass

        result = struct.pack(
            '<H6B',
            length,
            flag,
            command,
            0,
            0,
            0x07,
            self._next_seq()
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
    def build_onoff(item, onoff):
        """
        :param item: Light or Group object
        :param onoff: true/false
        :return: binary command to set on/off for the light/group
        """
        return item.build_command(
            COMMAND_ONOFF,
            struct.pack('<B', onoff)
        )

    @staticmethod
    def build_temp(item, temp, transition):
        """
        :param item: Light or Group object
        :param temp: colour temperature in kelvin
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return: binary command to set the light/group colour temperature
        """
        return item.build_command(
            COMMAND_TEMP,
            struct.pack('<HH', temp, transition)
        )

    @staticmethod
    def build_luminance(item, lum, transition):
        """
        :param item: Light or Group object
        :param lum: luminance (brightness)
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return: binary command to set the light/group luminance (brightness)
        """
        return item.build_command(
            COMMAND_LUMINANCE,
            struct.pack('<BH', lum, transition)
        )

    @staticmethod
    def build_colour(item, red, green, blue, transition):
        """
        :param item: Light or Group object
        :param red: amount of red
        :param green: amount of green
        :param blue: amount of blue
        :param transition: transition time in 1/10 seconds, 0 to disable
        :return: binary command to set the light/group RGB colour
        """
        return item.build_command(
            COMMAND_COLOUR,
            struct.pack('<BBBBH', red, green, blue, DEFAULT_ALPHA, transition)
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

    def update_group_list(self, throttling_interval=None):
        """ update all groups

        :param throttling_interval: optional throttling interval (skip call to
            gateway if last call finished less than throttling interval seconds
            ago)
        :return: dict from group name to Group object of newly
                 discovered groups
        """
        if (throttling_interval and
                time.time() < self.__groups_updated + throttling_interval):
            return {}

        with self.__lock:
            if (throttling_interval and
                    time.time() < self.__groups_updated + throttling_interval):
                return {}

            command = self.build_group_list()
            data = self.send(command)

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of groups: %d', num)
            new_groups = {}

            for i in range(0, num):
                pos = 9 + i * 18
                payload = data[pos:pos + 18]

                (idx, name) = struct.unpack('<H16s', payload)
                name = name.decode('utf-8').replace('\0', '')

                if name in self.__groups and self.__groups[name].idx() == idx:
                    group = self.__groups[name]
                    self.__logger.debug('Old group %d: %s', idx, name)
                else:
                    group = Group(self, idx, name)
                    self.__logger.debug('New group %d: %s', idx, name)

                new_groups[name] = group

            for name in self.__groups:
                if (name not in new_groups or
                        self.__groups[name].idx() != new_groups[name].idx()):
                    self.__groups[name].mark_deleted()
                    del self.__groups[name]
                else:
                    del new_groups[name]

            for name in new_groups:
                self.__groups[name] = new_groups[name]

            self.update_group_lights()
            self.update_group_scenes()
            self.__groups_updated = time.time()
            return new_groups

    def _lights_sorted_byidx(self):
        """ get the lights sorted by light idx
            needed to keep lists of group lights backward compatible with
            the previous version of the library in order not to break existing
            integrations

        :return: list of light mac addresses sorted by light idx
        """
        return [i[0] for i in sorted(
            [(light.addr(), light.idx())
             for light in self.__lights.values()], key=lambda i: i[1])]

    def update_group_lights(self):
        """ update the list of group's light mac addresses for all groups

        :return:
        """
        for group in self.__groups.values():
            lights = [addr for addr in self._lights_sorted_byidx()
                      if group.idx() in self.__lights[addr].groups()]
            group.set_lights(lights)
            group.update_status()

    def update_group_scenes(self):
        """ update the list of group's scenes for all groups

        :return:
        """
        for group in self.__groups.values():
            scenes = [name for name in self.__scenes
                      if group.idx() == self.__scenes[name].group()]
            group.set_scenes(scenes)

    def group_info(self, group):
        """ get the list of group's light mac addresses
            deprecated, for backward compatibility only!

        :param group: Group object
        :return: list of group's light mac addresses
        """
        self.update_all_light_status()
        return group.lights()

    def update_scene_list(self, throttling_interval=None):
        """ update all scenes

        :param throttling_interval: optional throttling interval (skip call to
            gateway if last call finished less than throttling interval seconds
            ago)
        :return: dict from scene name to Scene object of newly
                 discovered scenes
        """
        if (throttling_interval and
                time.time() < self.__scenes_updated + throttling_interval):
            return {}

        with self.__lock:
            if (throttling_interval and
                    time.time() < self.__scenes_updated + throttling_interval):
                return {}

            command = self.build_scene_list()
            data = self.send(command)

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of scenes: %d', num)
            new_scenes = {}

            for i in range(0, num):
                pos = 9 + i * 20
                payload = data[pos:pos + 20]

                (idx, name, group) = struct.unpack('<Bx16sH', payload)
                name = name.decode('utf-8').replace('\0', '')
                group = 16 - format(group, '016b').index('1')

                if (name in self.__scenes and
                        self.__scenes[name].idx() == idx and
                        self.__scenes[name].group() == group):
                    scene = self.__scenes[name]
                    self.__logger.debug('Old scene %d: %s, group: %d', idx,
                                        name, group)
                else:
                    scene = Scene(self, idx, name, group)
                    self.__logger.debug('New scene %d: %s, group: %d', idx,
                                        name, group)

                new_scenes[name] = scene

            for name in self.__scenes:
                if (name not in new_scenes or
                        self.__scenes[name].idx() != new_scenes[name].idx() or
                        self.__scenes[name].group() !=
                        new_scenes[name].group()):
                    self.__scenes[name].mark_deleted()
                    del self.__scenes[name]
                else:
                    del new_scenes[name]

            for name in new_scenes:
                self.__scenes[name] = new_scenes[name]

            self.__scenes_updated = time.time()
            return new_scenes

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
                    self._connect()
                    return self.send(data, reconnect=False)

                raise err

            return total_received_data

    def update_light_status(self, light):
        """ get the status of the given light (only subset of values)
            deprecated, for backward compatibility only!

        :param light: Light object
        :return: tuple containing (onoff, lum, temp, red, green, blue)
        """
        with self.__lock:
            command = self.build_light_status(light)
            data = self.send(command)

            unreachable_data_len = 18
            if len(data) == unreachable_data_len:
                return None, None, None, None, None, None

            (onoff, lum, temp, red, green, blue) = struct.unpack(
                '<19x2BH3B4x', data)

            self.__logger.debug('Light: %x', light.addr())
            self.__logger.debug('onoff: %d', onoff)
            self.__logger.debug('lum:   %d', lum)
            self.__logger.debug('temp:  %d', temp)
            self.__logger.debug('red:   %d', red)
            self.__logger.debug('green: %d', green)
            self.__logger.debug('blue:  %d', blue)

            return onoff, lum, temp, red, green, blue

    def update_all_light_status(self, throttling_interval=None):
        """ update the status of all lights

        :param throttling_interval: optional throttling interval (skip call to
            gateway if last call finished less than throttling interval seconds
            ago)
        :return: dict from light mac address to Light object of newly
                 discovered lights
        """
        if (throttling_interval and
                time.time() < self.__lights_updated + throttling_interval):
            return {}

        with self.__lock:
            if (throttling_interval and
                    time.time() < self.__lights_updated + throttling_interval):
                return {}

            command = self.build_all_light_status()
            data = self.send(command)

            old_hash = self.__lights_hash
            self.__lights_hash = hashlib.md5(data[7:]).hexdigest()
            if old_hash == self.__lights_hash:
                self.__lights_updated = time.time()
                return {}

            (num,) = struct.unpack('<H', data[7:9])
            self.__logger.debug('Number of lights: %d', num)
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
                    return {}

                (type_id, version, reachable, groups, onoff, lum, temp, red,
                 green, blue) = struct.unpack('<B4sBH2BH3Bx', stat)
                name = name.decode('utf-8').replace('\0', '')
                groups = [16 - j for j, val
                          in enumerate(format(groups, '016b')) if val == '1']
                version = format(struct.unpack('>I', version)[0], '032b')
                version = ''.join('{0:01X}'.format(
                    int(version[i * 4:(i + 1) * 4], 2)) for i in range(8))

                if addr in self.__lights:
                    light = self.__lights[addr]
                    self.__logger.debug('Old light: %x', addr)
                else:
                    if type_id not in self.__device_types:
                        self.__logger.warning(
                            'Unknown device type id: %s. Please report to '
                            'https://github.com/tfriedel/python-lightify',
                            type_id)
                        if (red, green, blue) == NO_RGB_VALUES:
                            type_id_assumed = TYPE_LIGHT_TUNABLE_WHITE
                        else:
                            type_id_assumed = TYPE_LIGHT_RGB
                    else:
                        type_id_assumed = type_id

                    light = Light(self, addr, type_id, type_id_assumed)
                    self.__logger.debug('New light: %x', addr)

                self.__logger.debug('name:      %s', name)
                self.__logger.debug('reachable: %d', reachable)
                self.__logger.debug('last seen: %d', last_seen)
                self.__logger.debug('onoff:     %d', onoff)
                self.__logger.debug('lum:       %d', lum)
                self.__logger.debug('temp:      %d', temp)
                self.__logger.debug('red:       %d', red)
                self.__logger.debug('green:     %d', green)
                self.__logger.debug('blue:      %d', blue)
                self.__logger.debug('type id:   %d', type_id)
                self.__logger.debug('groups:    %s', groups)
                self.__logger.debug('version:   %s', version)
                self.__logger.debug('idx:   %s', i)

                light.update_status(reachable, last_seen, onoff, lum, temp,
                                    red, green, blue, name, groups, version, i)
                new_lights[addr] = light

            for addr in self.__lights:
                if addr not in new_lights:
                    self.__lights[addr].mark_deleted()
                    del self.__lights[addr]
                else:
                    del new_lights[addr]

            for addr in new_lights:
                self.__lights[addr] = new_lights[addr]

            self.__lights_updated = time.time()
            self.__lights_changed = self.__lights_updated
            return new_lights
