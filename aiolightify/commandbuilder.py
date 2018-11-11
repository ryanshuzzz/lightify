import struct
import binascii

class CommandBuilder(object):
    def __init__(self):
        self.__seq = 1

    def next_seq(self):
        self.__seq = (self.__seq + 1) % 256
        return self.__seq

    def build_global_command(self, command, data):
        length = 6 + len(data)
        if type(data) is str:
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

    def build_basic_command(self, flag, command, group_or_light, data):
        length = 14 + len(data)
        if type(data) is str:
            data = data.encode('cp437')
        if type(group_or_light) is str:
            group_or_light = group_or_light.decode('cp437')
        result = struct.pack(
            "<H6B",
            length,
            flag,
            command,
            0,
            0,
            0x7,
            self.next_seq()
        ) + group_or_light + data

        return result

    def build_command(self, command, group, data):
        # length = 14 + len(data)

        return self.build_basic_command(
            0x02,
            command,
            struct.pack("<8B", group.idx(), 0, 0, 0, 0, 0, 0, 0),
            data)

    def build_light_command(self, command, light, data):
        # length = 6 + 8 + len(data)

        return self.build_basic_command(
            0x00,
            command,
            struct.pack("<Q", light.addr()),
            data
        )