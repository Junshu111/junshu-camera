"""LAN discovery rejects a VPN/public/down adapter and fails closed offline."""
import copy,json,subprocess,unittest
from unittest.mock import patch

class NetworkTests(unittest.TestCase):
    def discover(self, addresses, routes):
        from camera_r4 import share_network
        def run(argv,**kwargs):
            self.assertEqual(argv[0],'ip')
            self.assertIn('-j',argv);self.assertIn('-4',argv)
            self.assertLessEqual(kwargs['timeout'],2)
            self.assertFalse(kwargs.get('shell',False))
            if argv[-2:]==['address','show']:
                output=copy.deepcopy(addresses)
                # iproute2 print_linkinfo: IPv4-filtered output omits link
                # metadata unless details is requested. Do not invent fields
                # for a command which cannot provide them (Share1 regression).
                if '-details' not in argv and '-d' not in argv:
                    for entry in output:
                        entry.pop('link_type',None);entry.pop('linkinfo',None)
            elif argv[-2:]==['route','show']:output=routes
            else:raise AssertionError('Unexpected network command: '+repr(argv))
            return subprocess.CompletedProcess(argv,0,json.dumps(output), '')
        with patch('camera_r4.share_network.subprocess.run',side_effect=run):
            return share_network.discover_lan_addresses()
    def test_connected_wifi_is_detected_with_ipv4_filtered_iproute2_output(self):
        addresses=[{'ifindex':3,'ifname':'wlan0','flags':['BROADCAST','MULTICAST','UP','LOWER_UP'],
                    'mtu':1500,'qdisc':'fq_codel','operstate':'UP','group':'default','txqlen':1000,
                    'link_type':'ether','addr_info':[{'family':'inet','local':'192.168.8.27',
                    'prefixlen':24,'broadcast':'192.168.8.255','scope':'global','dynamic':True,
                    'noprefixroute':True,'label':'wlan0','valid_life_time':83515,'preferred_life_time':83515}]}]
        routes=[{'dst':'default','gateway':'192.168.8.1','dev':'wlan0','protocol':'dhcp','metric':600},
                {'dst':'192.168.8.0/24','dev':'wlan0','protocol':'kernel','scope':'link','prefsrc':'192.168.8.27','metric':600}]
        choices=self.discover(addresses,routes)
        self.assertEqual([(x.interface,x.address) for x in choices],[('wlan0','192.168.8.27')])
    def test_detailed_virtual_link_metadata_remains_excluded(self):
        choices=self.discover([{'ifname':'eth9','flags':['UP','LOWER_UP'],'operstate':'UP',
            'link_type':'ether','linkinfo':{'info_kind':'veth'},
            'addr_info':[{'family':'inet','local':'192.168.8.9','scope':'global'}]}],
            [{'dst':'192.168.8.0/24','dev':'eth9'}])
        self.assertEqual(choices,[])
    def test_only_active_rfc1918_ethernet_wifi_with_route_evidence(self):
        def entry(name,ip,state='UP',kind='ether'):
            return {'ifname':name,'flags':['UP','LOWER_UP'],'operstate':state,'link_type':kind,'addr_info':[{'family':'inet','local':ip,'scope':'global'}]}
        addresses=[entry('wlan0','192.168.8.4'),entry('eth0','10.2.1.8'),entry('wg0','10.5.0.1'),entry('tun0','172.16.1.2'),entry('eth2','8.8.8.8'),entry('eth3','192.168.4.3','DOWN'),entry('lo','127.0.0.1'),entry('eth4','192.168.7.5')]
        routes=[{'dst':'default','dev':name} for name in ['wlan0','eth0','wg0','tun0','eth2','eth3','lo']]
        result=self.discover(addresses,routes)
        self.assertEqual([(item.interface,item.address) for item in result],[('wlan0','192.168.8.4'),('eth0','10.2.1.8')])
    def test_tunnel_kind_disguised_as_ethernet_is_rejected(self):
        result=self.discover([{'ifname':'eth0','flags':['UP'],'operstate':'UP','link_type':'ether','linkinfo':{'info_kind':'wireguard'},'addr_info':[{'family':'inet','local':'10.0.0.1','scope':'global'}]}],[{'dst':'default','dev':'eth0'}])
        self.assertEqual(result,[])
    def test_command_missing_or_invalid_output_is_offline(self):
        from camera_r4.share_network import discover_lan_addresses
        for failure in (FileNotFoundError(),subprocess.TimeoutExpired('ip',1.5)):
            with patch('camera_r4.share_network.subprocess.run',side_effect=failure):self.assertEqual(discover_lan_addresses(),[])
        with patch('camera_r4.share_network.subprocess.run',return_value=subprocess.CompletedProcess([],0,'not json','')):self.assertEqual(discover_lan_addresses(),[])

if __name__=='__main__':unittest.main()
