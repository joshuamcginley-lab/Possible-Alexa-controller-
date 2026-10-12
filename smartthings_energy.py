#!/usr/bin/env python3
"""Read power and energy from SmartThings devices (e.g. Samsung heat pumps).

1. Create a token at https://account.smartthings.com/tokens
   - Scopes: "Devices" -> List all devices, See all devices
   - Personal tokens expire after 24 hours; make a new one when it stops working.
2. Run:
    SMARTTHINGS_TOKEN=your-token python3 smartthings_energy.py

Read-only: it only lists devices and reads their status.
"""
import json
import os
import sys
import urllib.request

API = "https://api.smartthings.com/v1"
ENERGY_CAPS = {"powerConsumptionReport", "powerMeter", "energyMeter"}


def get(path, token):
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def val(status, cap, attr):
    return status.get(cap, {}).get(attr, {}).get("value")


def main():
    token = os.environ.get("SMARTTHINGS_TOKEN")
    if not token:
        sys.exit("Set SMARTTHINGS_TOKEN first (see the top of this file).")

    try:
        devices = get("/devices", token)["items"]
    except urllib.error.HTTPError as e:
        sys.exit(f"SmartThings API error {e.code}: token wrong, expired, or missing device scopes.")

    print(f"Found {len(devices)} SmartThings devices.\n")
    for d in devices:
        caps = {c["id"] for comp in d.get("components", []) for c in comp.get("capabilities", [])}
        status = get(f"/devices/{d['deviceId']}/status", token)["components"].get("main", {})
        label = d.get("label") or d.get("name")
        print(f"{label}  [{d.get('manufacturerName', '')} {d.get('deviceTypeName') or ''}]".rstrip())

        mode = val(status, "airConditionerMode", "airConditionerMode")
        if mode:
            print(f"    mode: {mode}, power: {val(status, 'switch', 'switch')}, "
                  f"room: {val(status, 'temperatureMeasurement', 'temperature')}, "
                  f"setpoint: {val(status, 'thermostatCoolingSetpoint', 'coolingSetpoint')}")

        if not caps & ENERGY_CAPS:
            print("    energy: not reported\n")
            continue

        report = val(status, "powerConsumptionReport", "powerConsumption")
        if report:
            total = report.get("energy")
            print(f"    live power: {report.get('power')} W")
            if total is not None:
                print(f"    lifetime energy: {total / 1000:.1f} kWh")
            if report.get("deltaEnergy") is not None:
                print(f"    last interval: {report['deltaEnergy']} Wh "
                      f"({report.get('start')} -> {report.get('end')})")
        if val(status, "powerMeter", "power") is not None:
            print(f"    power meter: {val(status, 'powerMeter', 'power')} W")
        if val(status, "energyMeter", "energy") is not None:
            print(f"    energy meter: {val(status, 'energyMeter', 'energy')} kWh")
        print()


if __name__ == "__main__":
    main()
