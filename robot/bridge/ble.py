"""BlueZ GATT adapter. Uses Debian's python3-dbus and python3-gi."""
import argparse
import logging
import os
from pathlib import Path
import signal

import dbus
import dbus.exceptions
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from control import Controller, MAX_FRAME, Robot

BLUEZ = "org.bluez"
PROPS = "org.freedesktop.DBus.Properties"
OBJECTS = "org.freedesktop.DBus.ObjectManager"
SERVICE = "org.bluez.GattService1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
ADVERTISEMENT = "org.bluez.LEAdvertisement1"
ROOT = "/org/microduck/iphone"
SERVICE_UUID = "8574C501-404B-4843-AF14-5195053B00A8"
RPC_UUID = "8574C502-404B-4843-AF14-5195053B00A8"


class Export(dbus.service.Object):
    def __init__(self, bus, path, interface):
        super().__init__(bus, path)
        self.path, self.interface = path, interface

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != self.interface:
            raise dbus.exceptions.DBusException("Unknown interface", name="org.freedesktop.DBus.Error.UnknownInterface")
        return self.properties()


class Service(Export):
    def properties(self):
        return {"UUID": SERVICE_UUID, "Primary": dbus.Boolean(True)}


class Pipe(Export):
    def __init__(self, bus, path, service, controller):
        super().__init__(bus, path, CHARACTERISTIC)
        self.service, self.controller = service, controller
        self.notifying = False
        self.buffers = {}

    def properties(self):
        return {"UUID": RPC_UUID, "Service": dbus.ObjectPath(self.service),
                "Flags": dbus.Array(["write", "notify"], signature="s"),
                "Notifying": dbus.Boolean(self.notifying)}

    @dbus.service.signal(PROPS, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changes, invalidated):
        pass

    @dbus.service.method(CHARACTERISTIC, in_signature="", out_signature="")
    def StartNotify(self):
        self.notifying = True
        self.PropertiesChanged(CHARACTERISTIC, {"Notifying": dbus.Boolean(True)}, [])

    @dbus.service.method(CHARACTERISTIC, in_signature="", out_signature="")
    def StopNotify(self):
        self.notifying = False
        self.buffers.clear()
        self.controller.disconnect()
        self.PropertiesChanged(CHARACTERISTIC, {"Notifying": dbus.Boolean(False)}, [])

    def peer_down(self, interface, changed, invalidated, path=None):
        if interface == "org.bluez.Device1" and changed.get("Connected") == dbus.Boolean(False):
            self.buffers.pop(path, None)
            self.controller.disconnect(path)

    @dbus.service.method(CHARACTERISTIC, in_signature="aya{sv}", out_signature="")
    def WriteValue(self, value, options):
        peer = str(options.get("device", ""))
        if (not self.notifying or not peer.startswith("/org/bluez/")
                or int(options.get("offset", 0)) != 0):
            return
        if peer not in self.buffers and len(self.buffers) >= 8:
            return
        data = self.buffers.get(peer, b"") + bytes(value)
        if len(data) > MAX_FRAME:
            self.buffers.pop(peer, None)
            self.controller.disconnect(peer)
            return
        lines = data.split(b"\n")
        self.buffers[peer] = lines.pop()
        chunk_size = max(20, min(512, int(options.get("mtu", 23)) - 3))
        for line in lines:
            response = self.controller.receive(peer, line)
            if response is not None:
                for offset in range(0, len(response), chunk_size):
                    chunk = response[offset:offset + chunk_size]
                    self.PropertiesChanged(CHARACTERISTIC, {"Value": dbus.ByteArray(chunk)}, [])


class Application(dbus.service.Object):
    def __init__(self, bus, controller):
        super().__init__(bus, ROOT)
        self.service = Service(bus, ROOT + "/service0", SERVICE)
        self.pipe = Pipe(bus, self.service.path + "/rpc", self.service.path, controller)

    @dbus.service.method(OBJECTS, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {item.path: {item.interface: item.properties()} for item in (self.service, self.pipe)}


class Advert(Export):
    def __init__(self, bus, path, name):
        super().__init__(bus, path, ADVERTISEMENT)
        self.name = name

    def properties(self):
        return {"Type": "peripheral", "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s"),
                "LocalName": self.name}

    @dbus.service.method(ADVERTISEMENT, in_signature="", out_signature="")
    def Release(self):
        pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--socket", default="/run/robotd.sock")
    args = parser.parse_args()
    if args.key.stat().st_mode & 0o077:
        parser.error("pairing key must be accessible only to its owner")
    key = bytes.fromhex(args.key.read_text().strip())
    import ai_input_lease
    robot = Robot(args.socket)
    robot.start()
    # The vendor helper's overall cap applies to one autonomous action.
    # Phone ownership instead expires on lost authenticated traffic. Keep
    # the helper's short file expiry, refresh, and physical-pad cancellation.
    ai_input_lease.MAX_ACTION_S = float("inf")
    controller = Controller(key, robot, lambda: ai_input_lease.InputLease(lambda: False))
    serve(controller, "Duck RC")


def serve(controller, name):
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    root = dbus.Interface(bus.get_object(BLUEZ, "/"), OBJECTS)
    adapter_path = next((path for path, interfaces in root.GetManagedObjects().items()
                         if "org.bluez.GattManager1" in interfaces
                         and "org.bluez.LEAdvertisingManager1" in interfaces), None)
    if adapter_path is None:
        raise RuntimeError("No BlueZ adapter with GATT and advertising")
    adapter = bus.get_object(BLUEZ, adapter_path)
    gatt = dbus.Interface(adapter, "org.bluez.GattManager1")
    advertiser = dbus.Interface(adapter, "org.bluez.LEAdvertisingManager1")
    app = Application(bus, controller)
    advert = Advert(bus, ROOT + "/advert", name)
    bus.add_signal_receiver(app.pipe.peer_down, signal_name="PropertiesChanged", dbus_interface=PROPS,
                            bus_name=BLUEZ, path_keyword="path")
    loop = GLib.MainLoop()

    def failure(error):
        print("BlueZ registration failed:", error, flush=True)
        loop.quit()
        os._exit(1)

    def advertise():
        advertiser.RegisterAdvertisement(advert.path, {}, reply_handler=lambda: print("BLE ready", flush=True),
                                         error_handler=failure)

    gatt.RegisterApplication(ROOT, {}, reply_handler=advertise, error_handler=failure)

    def tick():
        controller.tick()
        return GLib.SOURCE_CONTINUE

    def stop(*_):
        controller.disconnect()
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLib.timeout_add(50, tick)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    loop.run()


if __name__ == "__main__":
    main()
