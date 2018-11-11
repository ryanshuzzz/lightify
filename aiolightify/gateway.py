import asyncio

from aiohttp import client_exceptions

from .config import Config
from .groups import Groups
from .lights import Lights
from .scenes import Scenes
from .errors import raise_error, ResponseError, RequestError
from .commandbuilder import CommandBuilder

import binascii
import struct
import logging
from collections import defaultdict
from enum import Enum


__version__ = '1.0.6.1'

MODULE = __name__
PORT = 4000

COMMAND_ALL_LIGHT_STATUS = 0x13
COMMAND_GROUP_LIST = 0x1e
COMMAND_GROUP_INFO = 0x26
COMMAND_LUMINANCE = 0x31
COMMAND_ONOFF = 0x32
COMMAND_TEMP = 0x33
COMMAND_COLOUR = 0x36
COMMAND_LIGHT_STATUS = 0x68

# Commands
# 13 all light status (returns list of light address, light status, light name)
# 1e group list (returns list of group id, and group name)
# 26 group status (returns group id, group name, and list of light addresses)
# 31 set group luminance
# 32 set group onoff
# 33 set group temp
# 36 set group colour
# 68 light status (returns light address and light status (?))

MAX_TEMPERATURE = 8000
MIN_TEMPERATURE = 1000
MAX_LUMINANCE = 100
MAX_COLOR = 255
TIMEOUT = 10  # timeout in seconds when communicating with the gateway


class DeviceType(Enum):
    LIGHT = 1
    PLUG = 2
    MOTIONSENSOR = 3
    SWITCH = 4


id_to_devicetype = defaultdict(lambda: DeviceType.LIGHT)
id_to_devicetype.update({10: DeviceType.LIGHT, 16: DeviceType.PLUG,
                         32: DeviceType.MOTIONSENSOR, 64: DeviceType.SWITCH,
                         65: DeviceType.SWITCH})




class Gateway:
    """Control a Lightify gateway."""

    def __init__(self, host):
        self.__logger = logging.getLogger(MODULE)
        self.__logger.addHandler(logging.NullHandler())
        self.__logger.info("Logging %s", MODULE)

        self.host = host
        self.__reader = None
        self.__writer = None

        self.config = None
        self.groups = None
        self.lights = None
        self.scenes = None


        # a sequence number which is used to number commands
        # sent to the gateway
        self.command_builder = CommandBuilder()
        self.__groups = {}
        self.__lights = {}

        # self.capabilities = None
        # self.rules = None
        # self.schedules = None
        # self.sensors = None

    async def connect(self):
        """ trys to establish a connection with the lightify gateway
        """
        self.__reader, self.__writer = await asyncio.open_connection(self.host, PORT)

    async def initialize(self):
        try:
            result = await self.connect()
            #result = await self.request('get', '')
        except client_exceptions.ClientError:
            raise RequestError(
                'Unable to connect to {}'.format(self.host)) from None

        self.config = Config(result['config'], self.send)
        self.groups = Groups(result['groups'], self.send)
        self.lights = Lights(result['lights'], self.send)
        # self.scenes = Scenes(result['scenes'], self.request)

    # async def request(self, method, path, json=None, auth=True):
    #     """Make a request to the API."""
    #     url = 'http://{}/api/'.format(self.host)
    #     if auth:
    #         url += '{}/'.format(self.username)
    #     url += path
    #
    #     async with self.websession.request(method, url, json=json) as res:
    #         if res.content_type != 'application/json':
    #             raise ResponseError(
    #                 'Invalid content type: {}'.format(res.content_type))
    #         data = await res.json()
    #         # _raise_on_error(data)
    #         return data



    # def __del__(self):
    #     try:
    #         self.__sock.shutdown(socket.SHUT_RDWR)
    #     except OSError:
    #         pass
    #     self.__sock.close()


    def groups(self):
        """Dict from group name to Group object."""
        return self.__groups

    def lights_(self):
        """Dict from light addr to Light object."""
        return self.__lights

    def light_byname(self, name):
        self.__logger.debug(len(self.lights()))

        for _, light in self.lights().items():
            if light.name() == name:
                return light

        return None



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

    def build_group_info(self, group):
        return self.build_command(COMMAND_GROUP_INFO, group, "".encode('cp437'))

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

    async def group_list(self):
        groups = {}
        data = self.build_group_list()
        data = await self.send(data)
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

    async def update_group_list(self):
        lst = await self.group_list()
        groups = {}

        for (idx, name) in lst.items():
            group = Group(self, self.__logger, idx, name)
            group_info = await self.group_info(group)
            group.set_lights(group_info)

            groups[name] = group

        self.__groups = groups

    async def group_info(self, group):
        lights = []
        data = self.build_group_info(group)
        data = await self.send(data)
        payload = data[7:]
        (idx, name, num) = struct.unpack("<H16sB", payload[:19])
        name = name.decode('utf-8').replace('\0', "")
        self.__logger.debug("Idx %d: '%s' %d", idx, name, num)
        for i in range(0, num):
            pos = 7 + 19 + i * 8
            payload = data[pos:pos + 8]
            (addr,) = struct.unpack("<Q", payload[:8])
            self.__logger.debug("%d: %x", i, addr)

            lights.append(addr)

        # self.read_light_status(addr)
        return lights

    async def send(self, data, reconnect=True):
        """  sends the packet 'data' to the gateway and returns the
             received packet.
        :param data: a string containing binary data
        :param reconnect: if true, will try to reconnect once. if false,
                          will raise an socket.error
        :return: received packet
        """

        #try
        # send
        self.__logger.debug('sending "%s"', binascii.hexlify(data))
        self.__writer.write(data)
        await self.__writer.drain()
        #self.__sock.sendall(data)

        # receive
        lengthsize = 2
        received_data = await self.__reader.read(lengthsize)
        (length,) = struct.unpack("<H", received_data[:lengthsize])

        self.__logger.debug(len(received_data))
        string = ""
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
            received_data = await self.__reader.read(expected)
            total_received_data += received_data
            expected -= len(received_data)
        self.__logger.debug('received %s', repr(total_received_data))

        # except socket.error as e:
        #     self.__logger.warning('lost connection to lightify gateway.')
        #     self.__logger.warning('socketError: {}'.format(str(e)))
        #     if reconnect:
        #         self.__logger.warning('Trying to reconnect.')
        #         self.connect()
        #         return self.send(data, reconnect=False)
        #     else:
        #         raise e
        return total_received_data

    async def update_light_status(self, light):
        data = self.build_light_status(light)
        data = await self.send(data)

        # (on, lum, temp, r, g, b, h) = struct.unpack("<27x2BH4B16x", data)
        (on, lum, temp, r, g, b, h) = struct.unpack("<19x2BH4B3x", data)
        self.__logger.debug(
            'status: %0x %0x %d %0x %0x %0x %0x', on, lum, temp, r, g, b, h)
        self.__logger.debug('onoff: %d', on)
        self.__logger.debug('temp:  %d', temp)
        self.__logger.debug('lum:   %d', lum)
        self.__logger.debug('red:   %d', r)
        self.__logger.debug('green: %d', g)
        self.__logger.debug('blue:  %d', b)
        return on, lum, temp, r, g, b

    async def update_all_light_status(self):

        data = self.build_all_light_status(1)
        data = await self.send(data)
        (num,) = struct.unpack("<H", data[7:9])

        self.__logger.debug('num: %d', num)

        old_lights = self.__lights
        new_lights = {}

        status_len = 50
        for i in range(0, num):
            pos = 9 + i * status_len
            payload = data[pos:pos + status_len]

            self.__logger.debug("%d %d %d", i, pos, len(payload))
            try:
                (a, addr, stat, name, time_offline, extra) = struct.unpack("<HQ16s16sH6s",
                                                             payload)
            except struct.error as e:
                self.__logger.warning(
                    "couldn't unpack light status packet.")
                self.__logger.warning("struct.error: {}".format(str(e)))
                self.__logger.warning(
                    "payload: {}".format(binascii.hexlify(payload)))
                return
            try:
                name = name.replace('\0', "")
            except TypeError:
                # Names are UTF-8 encoded, but not data.
                name = name.decode('utf-8').replace('\0', "")

            self.__logger.debug('light: %x %x %s', a, addr, name )


            if addr in old_lights:
                light = old_lights[addr]
            else:
                light = Light(self, self.__logger, addr, name)

            (device_type, ver1_1, ver1_2, ver1_3, ver1_4, ver1_5, zone_id,
             on, lum, temp, r, g, b, h) = struct.unpack("<6BH2BH4B", stat)
            version_string = "%02d%02d%02d%d%d" % (
                ver1_1, ver1_2, ver1_3, ver1_4, ver1_5)
            light.set_devicetype(id_to_devicetype[device_type])
            self.__logger.debug('status: %x %0x', b, h)
            self.__logger.debug('zone id: %x', zone_id)
            self.__logger.debug('onoff: %d', on)
            self.__logger.debug('temp:  %d', temp)
            self.__logger.debug('lum:   %d', lum)
            self.__logger.debug('red:   %d', r)
            self.__logger.debug('green: %d', g)
            self.__logger.debug('blue:  %d', b)
            self.__logger.debug('time offline: %d', time_offline)
            if time_offline>1:
                on = False
            light.update_status(on, lum, temp, r, g, b)
            new_lights[addr] = light
        # return (on, lum, temp, r, g, b)

        self.__lights = new_lights


# def _raise_on_error(data):
#     """Check response for error message."""
#     if isinstance(data, list):
#         data = data[0]
#
#     if isinstance(data, dict) and 'error' in data:
#         raise_error(data['error'])