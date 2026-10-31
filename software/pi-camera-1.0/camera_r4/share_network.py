"""Conservative, bounded, read-only discovery of reachable LAN candidates."""
from dataclasses import dataclass
import ipaddress
import json
import re
import subprocess

@dataclass(frozen=True)
class LanAddress:
    interface: str
    address: str

_PRIVATE=tuple(ipaddress.ip_network(value) for value in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))

def _read(kind):
    # With -4, iproute2 omits link_type/linkinfo unless details is requested.
    # Those fields are required below to distinguish Wi-Fi/Ethernet from tunnels.
    command=['ip','-j','-4']
    if kind=='address':command.append('-details')
    result=subprocess.run(command+[kind,'show'],capture_output=True,text=True,timeout=1.5,check=True)
    if len(result.stdout)>262144:raise ValueError('network output exceeds bound')
    value=json.loads(result.stdout)
    if not isinstance(value,list):raise ValueError('network output is not a list')
    return value

def discover_lan_addresses():
    """Never changes network; uncertain/tunnel/offline environments return no choices."""
    try:
        addresses=_read('address');routes=_read('route')
        routed={route.get('dev') for route in routes if isinstance(route,dict) and route.get('type','unicast')=='unicast' and route.get('dst')}
        choices=[]
        for entry in addresses:
            if not isinstance(entry,dict):continue
            name=entry.get('ifname','')
            # Physical Linux wireless/ethernet names only. Bridges, VPNs and
            # virtual/tunnel links are not offered even if RFC1918 addressed.
            if not re.fullmatch(r'(?:wlan\d+|wl[a-zA-Z0-9]+|eth\d+|en[a-zA-Z0-9]+)',name):continue
            if entry.get('operstate')!='UP' or 'UP' not in entry.get('flags',[]) or entry.get('link_type')!='ether':continue
            if entry.get('linkinfo',{}).get('info_kind') or name not in routed:continue
            for info in entry.get('addr_info',[]):
                if info.get('family')!='inet' or info.get('scope')!='global' or info.get('tentative') or info.get('deprecated'):continue
                address=ipaddress.IPv4Address(info.get('local',''))
                if any(address in net for net in _PRIVATE):
                    choice=LanAddress(name,str(address))
                    if choice not in choices:choices.append(choice)
        return choices
    except (OSError,ValueError,TypeError,AttributeError,subprocess.SubprocessError):
        return []
