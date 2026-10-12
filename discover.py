#!/usr/bin/env python3
"""Discover smart-home devices on the local network.

Run from a computer on your home Wi-Fi:
    python3 discover.py

Optional (better results via mDNS/Bonjour):
    pip install zeroconf

Read-only: it only listens for announcements and checks a few well-known
ports. It does not log in to or change anything.
"""
import ipaddress
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 0.6

# Ports that hint at a device type.
PORT_HINTS = {
    9999: "TP-Link Kasa (plug/switch, often energy-metering)",
    6668: "Tuya / Smart Life device",
    6053: "ESPHome device",
    1883: "MQTT broker",
    8123: "Home Assistant (already running!)",
    55443: "Amazon Echo (or Yeelight bulb)",
    38899: "WiZ bulb (UDP)",
    8080: "Generic web UI",
    80: "Web UI",
}

MDNS_TYPES = [
    "_hue._tcp.local.", "_hap._tcp.local.", "_matter._tcp.local.",
    "_matterc._udp.local.", "_shelly._tcp.local.", "_esphomelib._tcp.local.",
    "_googlecast._tcp.local.", "_amzn-wplay._tcp.local.", "_sonos._tcp.local.",
    "_ecobee._tcp.local.", "_daikin._tcp.local.", "_http._tcp.local.",
    "_home-assistant._tcp.local.", "_miio._udp.local.", "_nanoleafapi._tcp.local.",
]


def local_subnet():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip, ipaddress.ip_network(f"{ip}/24", strict=False)


def ssdp_scan(wait=4):
    """UPnP discovery: finds Hue bridges, Echo devices, TVs, routers, Wemo, etc."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n'
    ).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    sock.sendto(msg, ("239.255.255.250", 1900))
    found = {}
    end = time.time() + wait
    while time.time() < end:
        try:
            data, (ip, _) = sock.recvfrom(4096)
        except socket.timeout:
            continue
        headers = {}
        for line in data.decode(errors="ignore").split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().upper()] = v.strip()
        entry = found.setdefault(ip, set())
        for key in ("SERVER", "ST", "HUE-BRIDGEID"):
            if headers.get(key):
                entry.add(f"{key}: {headers[key]}")
    sock.close()
    return found


def kasa_scan(wait=3):
    """TP-Link Kasa broadcast query (returns model + whether it meters energy)."""
    def enc(s):
        key, out = 171, bytearray()
        for c in s.encode():
            key = key ^ c
            out.append(key)
        return bytes(out)

    def dec(b):
        key, out = 171, bytearray()
        for c in b:
            out.append(key ^ c)
            key = c
        return out.decode(errors="ignore")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    sock.sendto(enc('{"system":{"get_sysinfo":{}}}'), ("255.255.255.255", 9999))
    found = {}
    end = time.time() + wait
    while time.time() < end:
        try:
            data, (ip, _) = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            info = json.loads(dec(data))["system"]["get_sysinfo"]
            meter = "energy" in info.get("feature", "").lower() or "ENE" in info.get("feature", "")
            found[ip] = {f"Kasa {info.get('model')} '{info.get('alias')}'"
                         + (" [energy metering]" if meter else "")}
        except Exception:
            found[ip] = {"Kasa device"}
    sock.close()
    return found


def mdns_scan(wait=5):
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        print("  (zeroconf not installed - skipping mDNS. `pip install zeroconf` for more results)")
        return {}
    found = {}

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info:
                for addr in info.parsed_addresses():
                    if ":" not in addr:
                        found.setdefault(addr, set()).add(f"{type_.split('.')[0]}: {name.split('.')[0]}")

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, MDNS_TYPES, Listener())
    time.sleep(wait)
    zc.close()
    return found


def port_probe(network):
    def probe(ip):
        hits = set()
        for port, label in PORT_HINTS.items():
            if port == 38899:  # UDP, skip in TCP probe
                continue
            with socket.socket() as s:
                s.settimeout(TIMEOUT)
                if s.connect_ex((str(ip), port)) == 0:
                    hits.add(f"port {port}: {label}")
        return str(ip), hits

    found = {}
    with ThreadPoolExecutor(max_workers=64) as pool:
        for ip, hits in pool.map(probe, network.hosts()):
            if hits:
                found[ip] = hits
    return found


def hostname(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def merge(*sources):
    out = {}
    for src in sources:
        for ip, notes in src.items():
            out.setdefault(ip, set()).update(notes)
    return out


def main():
    my_ip, net = local_subnet()
    print(f"Scanning {net} from {my_ip}...\n")
    print("- UPnP/SSDP..."); ssdp = ssdp_scan()
    print("- Kasa broadcast..."); kasa = kasa_scan()
    print("- mDNS/Bonjour..."); mdns = mdns_scan()
    print("- Port probe (about 30s)..."); ports = port_probe(net)

    results = merge(ssdp, kasa, mdns, ports)
    print(f"\nFound {len(results)} devices:\n")
    for ip in sorted(results, key=lambda x: ipaddress.ip_address(x)):
        name = hostname(ip)
        print(f"{ip:<16} {name}")
        for note in sorted(results[ip]):
            print(f"    {note}")

    with open("devices.json", "w") as f:
        json.dump({ip: {"hostname": hostname(ip), "notes": sorted(n)}
                   for ip, n in results.items()}, f, indent=2)
    print("\nSaved to devices.json - paste that file back to Claude.")


if __name__ == "__main__":
    main()
