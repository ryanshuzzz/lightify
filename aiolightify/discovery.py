from .bridge import Bridge

URL_NUPNP = 'https://www.meethue.com/api/nupnp'


async def discover_nupnp():
    #@TODO: implement discovery
    """Discover bridges via NUPNP."""
    # async with websession.get(URL_NUPNP) as res:
    #     return [Bridge(item['internalipaddress'], websession=websession)
    #             for item in (await res.json())]
    return [Bridge('192.168.1.100')]