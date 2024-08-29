import asyncio
import itertools
from typing import Any, AbstractSet

from cachetools import cached, TTLCache
import shapely

import scrypted_sdk
from scrypted_sdk import ScryptedDeviceBase, MixinProvider, ScryptedDeviceType, ScryptedInterface, WritableDeviceState, ScryptedDevice, Notifier, NotifierOptions, MediaObject, Settings, Setting, Storage, Camera, ResponsePictureOptions, RequestPictureOptions


class PrefixStorage(Storage):

    def __init__(self, mixinProvider: ScryptedDeviceBase, prefix: str):
        self.mixinProvider = mixinProvider
        self.prefix = prefix

    def getItem(self, key: str) -> str:
        return self.mixinProvider.storage.getItem(f"{self.prefix}:{key}")

    def setItem(self, key: str, value: str):
        return self.mixinProvider.storage.setItem(f"{self.prefix}:{key}", value)

    def removeItem(self, key: str):
        return self.mixinProvider.storage.removeItem(f"{self.prefix}:{key}")

    def getKeys(self) -> AbstractSet[str]:
        keys = self.mixinProvider.storage.getKeys()
        return {key.removeprefix(f"{self.prefix}:") for key in keys if key.startswith(f"{self.prefix}:")}

    def clear(self):
        keys = self.getKeys()
        for key in keys:
            self.removeItem(key)


async def reload_settings(device_id, mixin) -> None:
    await scrypted_sdk.deviceManager.onMixinEvent(
        device_id,
        mixin,
        ScryptedInterface.Settings.value,
        None
    )


class MixinConsole:
    def __init__(self, mixinId: str, mixinProvider: ScryptedDeviceBase):
        self.mixinId = mixinId
        self.mixinProvider = mixinProvider
        self.nativeId = mixinProvider.nativeId
        asyncio.create_task(self.tryConnect())

    async def tryConnect(self) -> None:
        try:
            await self.connect()
        except Exception:
            import traceback
            traceback.print_exc()
            await self.reconnect()

    async def connect(self) -> None:
        ds = scrypted_sdk.deviceManager.getDeviceState(self.nativeId)
        if not ds:
            return

        plugins = await scrypted_sdk.systemManager.getComponent('plugins')
        mixin = scrypted_sdk.systemManager.getDeviceById(self.mixinId)

        if not mixin:
            return

        pluginId = mixin.pluginId
        mixinNativeId = mixin.nativeId or 'undefined'

        port = await plugins.getRemoteServicePort(pluginId, 'console-writer')

        reader, self.writer = await asyncio.open_connection('localhost', port)
        self.writer.write((mixinNativeId + '\n').encode())
        await self.writer.drain()

    async def reconnect(self) -> None:
        try:
            if self.writer:
                self.writer.close()
                await self.writer.wait_closed()
        except:
            pass
        finally:
            self.writer = None

        await asyncio.sleep(10000)
        asyncio.create_task(self.tryConnect())

    async def log(self, *args):
        self.mixinProvider.print(*args)
        try:
            if not self.writer:
                return

            message = " ".join([str(arg) for arg in args])
            message = message.replace('\n', f'\n[{self.mixinProvider.name}]: ')
            self.writer.write((f'[{self.mixinProvider.name}]: ' + message + '\n').encode())
            await self.writer.drain()
        except:
            asyncio.create_task(self.reconnect())

    async def info(self, *args):
        await self.log(*args)

    async def error(self, *args):
        await self.log(*args)

    async def warn(self, *args):
        await self.log(*args)

    async def debug(self, *args):
        await self.log(*args)

    async def trace(self, *args):
        await self.log(*args)


mixin_consoles = {}
def getMixinConsole(mixinId, mixinProvider) -> Any:
    native_id_consoles = mixin_consoles.get(mixinProvider.nativeId)
    if not native_id_consoles:
        native_id_consoles = {}
        mixin_consoles[mixinProvider.nativeId] = native_id_consoles

    console = native_id_consoles.get(mixinId)
    if console:
        return console

    console = MixinConsole(mixinId, mixinProvider)
    native_id_consoles[mixinId] = console
    return console


class ShouldSendNotification(Exception):
    def __init__(self, reason):
        self.reason = reason


class NotificationFilterMixin(Notifier, Settings, Camera):

    def __init__(self, mixinProvider: 'NotificationFilter', mixinDevice: Any, mixinDeviceInterfaces: list[str], mixinDeviceState: WritableDeviceState):
        self.mixinProvider = mixinProvider
        self.mixinDevice = mixinDevice
        self.mixinDeviceInterfaces = mixinDeviceInterfaces
        self.mixinDeviceState = mixinDeviceState
        self.storage = PrefixStorage(mixinProvider, f"mixin:{mixinDeviceState.id}")
        self.mixinConsole = getMixinConsole(mixinDeviceState.id, mixinProvider)
        asyncio.create_task(reload_settings(mixinDeviceState.id, self))

    @property
    def selected_camera(self) -> list[str]:
        return self.storage.getItem("selected_camera")

    def zones_of(self, camera_id: str) -> list[str]:
        return self.storage.getItem(f"{camera_id}:zones") or []

    def zone_details_of(self, camera_id: str, zone: str) -> list[list[float]]:
        return self.storage.getItem(f"{camera_id}:zone:{zone}") or []

    def zone_type_of(self, camera_id: str, zone: str) -> str:
        return self.storage.getItem(f"{camera_id}:zone:{zone}:type") or "Intersect"

    async def sendNotification(self, title: str, options: NotifierOptions = None, media: str | MediaObject = None, icon: str | MediaObject = None) -> None:
        try:
            if not options:
                raise ShouldSendNotification("no options")

            if "recordedEvent" not in options:
                raise ShouldSendNotification("no recordedEvent")
            recordedEvent = options["recordedEvent"]

            if recordedEvent.get("id"):
                device_id = recordedEvent["id"]
            elif "data" in options and "snoozeId" in options["data"]:
                # TODO: remove this once we have the actual device id in the event
                device_id = options["data"]["snoozeId"].split("-")[1]
            else:
                raise ShouldSendNotification("no device id")

            zones = self.zones_of(device_id)
            if not zones:
                raise ShouldSendNotification("no zones")

            if "data" in recordedEvent and "detections" in recordedEvent["data"]:
                detections = recordedEvent["data"]["detections"]
            else:
                raise ShouldSendNotification("no detections")

            if "inputDimensions" not in recordedEvent["data"]:
                raise ShouldSendNotification("no inputDimensions")
            inputDimensions = recordedEvent["data"]["inputDimensions"]

            no_zones_at_all = True
            for detection in detections:
                if "boundingBox" not in detection:
                    continue

                boundingBox = detection["boundingBox"]
                detection_box = shapely.geometry.box(boundingBox[0], boundingBox[1], boundingBox[0] + boundingBox[2], boundingBox[1] + boundingBox[3])

                for zone in zones:
                    zone_details = self.zone_details_of(device_id, zone)
                    if not zone_details:
                        continue

                    zone_details = [[x * inputDimensions[0], y * inputDimensions[1]] for [x, y] in zone_details]
                    zone_box = shapely.geometry.Polygon(zone_details)
                    no_zones_at_all = False

                    if self.zone_type_of(device_id, zone) == "Intersect":
                        if detection_box.intersects(zone_box):
                            raise ShouldSendNotification(f"bounding box {detection_box} intersects zone {zone_box}")
                    else:
                        if detection_box.contains(zone_box):
                            raise ShouldSendNotification(f"bounding box {detection_box} contains zone {zone_box}")

            if no_zones_at_all:
                raise ShouldSendNotification("no detections or no zones")
        except ShouldSendNotification as e:
            await self.mixinConsole.info(f"Sending notification {title} because: {e.reason}")
            await self.mixinDevice.sendNotification(title, options, media, icon)
        except Exception as e:
            await self.mixinConsole.error(f"Failed to filter notification: {e}")
            await self.mixinDevice.sendNotification(title, options, media, icon)
        else:
            # nothing matched, so don't send
            await self.mixinConsole.info(f"Skipping notification: {title}")

    async def mySettings(self) -> list[Setting]:
        cameras = await self.get_all_detector_cameras()
        settings = [
            {
                "group": "Notification Zone Filter",
                "key": "selected_camera",
                "title": "Configure Zones for Camera",
                "description": "Select a camera to configure zones for.",
                "value": self.camera_to_readable(self.selected_camera),
                "choices": [self.camera_to_readable(camera_id) for camera_id in cameras],
                "immediate": True
            }
        ]

        if self.selected_camera:
            camera_id = self.selected_camera
            zones = self.zones_of(camera_id)
            settings.append({
                "group": "Notification Zone Filter",
                "key": f"{camera_id}:zones",
                "description": "Enter the name of a new zone or delete an existing zone.",
                "multiple": True,
                "combobox": True,
                "choices": zones,
                "value": zones,
            })
            zone_settings = itertools.chain(*[
                [
                    {
                        "group": "Notification Zone Filter",
                        "subgroup": f"Zone: {zone}",
                        "key": f"{camera_id}:zone:{zone}",
                        "title": "Open Zone Editor",
                        "type": "clippath",
                        "value": self.zone_details_of(self.selected_camera, zone)
                    },
                    {
                        "group": "Notification Zone Filter",
                        "subgroup": f"Zone: {zone}",
                        "key": f"{camera_id}:zone:{zone}:type",
                        "title": "Zone Type",
                        "choices": ["Intersect", "Contain"],
                        "description": "An Intersect zone will match objects that are partially or fully inside the zone. A Contain zone will only match objects that are fully inside the zone.",
                        "value": self.zone_type_of(self.selected_camera, zone)
                    }
                ] for zone in zones
            ])
            settings.extend(zone_settings)

        return settings

    async def getSettings(self) -> list[Setting]:
        parent_settings = []
        if ScryptedInterface.Settings.value in self.mixinDeviceInterfaces:
            parent_settings = await self.mixinDevice.getSettings()
        return parent_settings + await self.mySettings()

    async def putSetting(self, key: str, value: str | list[str] | list[list[float]]) -> None:
        my_settings = await self.mySettings()
        my_keys = [setting["key"] for setting in my_settings]

        if key not in my_keys:
            await self.mixinDevice.putSetting(key, value)
            return

        if key == "selected_camera":
            value = self.readable_to_camera(value)
        self.storage.setItem(key, value)
        await reload_settings(self.mixinDeviceState.id, self)

    async def getPictureOptions(self) -> list[ResponsePictureOptions]:
        camera = self.get_device_from_scrypted(self.selected_camera)
        if not camera:
            raise Exception("No camera selected")
        return await camera.getPictureOptions()

    async def takePicture(self, options: RequestPictureOptions = None) -> MediaObject:
        camera = self.get_device_from_scrypted(self.selected_camera)
        if not camera:
            raise Exception("No camera selected")
        return await camera.takePicture(options)

    async def get_all_detector_cameras(self) -> list[str]:
        detector_cameras = []
        state = scrypted_sdk.systemManager.getSystemState()
        for device_id in state.keys():
            device = self.get_device_from_scrypted(device_id)
            if not device:
                continue

            # prevent looping back to self
            if device.id in self.mixinProvider.all_mixin_device_ids():
                continue

            if ScryptedInterface.Camera.value in device.interfaces:# and \
                #ScryptedInterface.ObjectDetector.value in device.interfaces:
                detector_cameras.append(device.id)
        return detector_cameras

    @cached(cache=TTLCache(maxsize=1024, ttl=5))
    def get_device_from_scrypted(self, device_id: str) -> ScryptedDevice:
        if not device_id:
            return None
        return scrypted_sdk.systemManager.getDeviceById(device_id)

    def is_valid_camera(self, camera_id: str) -> bool:
        camera = self.get_device_from_scrypted(camera_id)
        return camera is not None and \
            ScryptedInterface.Camera.value in camera.interfaces# and \
            #ScryptedInterface.ObjectDetector.value in camera.interfaces

    def camera_to_readable(self, camera_id: str) -> str:
        camera = self.get_device_from_scrypted(camera_id)
        if not camera:
            return None
        return f"{camera.name} (id: {camera.id})"

    def readable_to_camera(self, readable: str) -> str:
        id = readable.split(" ")[-1]
        id = id.removeprefix("(id: ").removesuffix(")")
        return id


class NotificationFilter(ScryptedDeviceBase, MixinProvider):

    def __init__(self, nativeId: str | None = None):
        super().__init__(nativeId)
        self.mixin_dict = {}

    def print(self, *args):
        print(*args)

    def all_mixin_device_ids(self) -> list[str]:
        return list(self.mixin_dict.keys())

    async def canMixin(self, type: ScryptedDeviceType, interfaces: list[str]) -> None | list[str]:
        if (ScryptedInterface.Notifier.value in interfaces):
            return [ScryptedInterface.Notifier.value, ScryptedInterface.Settings.value, ScryptedInterface.Camera.value]
        return None

    async def getMixin(self, mixinDevice: ScryptedDevice, mixinDeviceInterfaces: list[str], mixinDeviceState: WritableDeviceState) -> Any:
        mixin = self.mixin_dict.get(mixinDeviceState.id)
        if not mixin:
            mixin = NotificationFilterMixin(self, mixinDevice, mixinDeviceInterfaces, mixinDeviceState)
            self.mixin_dict[mixinDeviceState.id] = mixin
        return mixin

    async def releaseMixin(self, id: str, mixinDevice: ScryptedDevice) -> None:
        return None


def create_scrypted_plugin():
    return NotificationFilter()