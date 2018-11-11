import logging
import asyncio
from pprint import pprint
import aiohttp
import async_timeout

from aiolightify.discovery import discover_nupnp

LIGHT1 = 'Lightstrip'

async def main():
    logging.basicConfig(level=logging.DEBUG)
    logging.info("Started")
    #with async_timeout.timeout(10):
    await run()

def get_addr_of_light(light_name, gateway):
    for light in gateway.lights().values():
        if light_name == light.name():
            return light
    return None

async def test_turn_light_on_and_off(gateway):
    # tests if a light can be turned off, on and off again and the state
    # is updated accordingly
    light = get_addr_of_light(LIGHT1, gateway)
    await light.set_onoff(0)
    asyncio.sleep(1)
    assert not light.on()
    await light.set_onoff(1)
    asyncio.sleep(1)
    assert light.on()
    await light.set_onoff(0)
    asyncio.sleep(1)
    assert not light.on()

async def run():
    bridges = await discover_nupnp()

    bridge = bridges[0]
    await bridge.connect()
    await bridge.update_all_light_status()
    await bridge.update_group_list()
    await test_turn_light_on_and_off(bridge)



    # await bridge.create_user('aiophue-example')
    # print('Your username is', bridge.username)
    #
    # await bridge.initialize()
    #
    # print('Name', bridge.config.name)
    # print('Mac', bridge.config.mac)
    #
    # print()
    # print('Lights:')
    # for id in bridge.lights:
    #     light = bridge.lights[id]
    #     print('{}: {}'.format(light.name, 'on' if light.state['on'] else 'off'))
    #
    # # Change state of a light.
    # await light.set_state(on=not light.state['on'])
    #
    # print()
    # print('Groups:')
    # for id in bridge.groups:
    #     group = bridge.groups[id]
    #     print('{}: {}'.format(group.name, 'on' if group.action['on'] else 'off'))
    #
    # # Change state of a group.
    # await group.set_action(on=not group.state['on'])
    #


asyncio.get_event_loop().run_until_complete(main())