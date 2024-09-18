import asyncio
from io import BytesIO
import itertools
from typing import Any, AbstractSet
import uuid

from cachetools import cached, TTLCache
from PIL import Image, ImageDraw
import shapely

import scrypted_sdk
from scrypted_sdk import (
    ScryptedDeviceBase,
    MixinProvider,
    ScryptedDeviceType,
    ScryptedInterface,
    WritableDeviceState,
    ScryptedDevice,
    Notifier,
    NotifierOptions,
    MediaObject,
    Settings,
    Setting,
    Storage,
    Camera,
    ResponsePictureOptions,
    RequestPictureOptions,
    DeviceProvider,
    DeviceCreator,
    DeviceCreatorSettings,
)


def draw_polygons_in_memory(image_bytes, polygon1, polygon2, color1, color2):
    """
    Draws two polygons on an image (in memory) with specified colors.

    Args:
        image_bytes (bytes): Input image in bytes.
        polygon1 (list): List of (x, y) tuples representing the vertices of the first polygon.
        polygon2 (list): List of (x, y) tuples representing the vertices of the second polygon.
        color1 (str or tuple): Color for the first polygon (e.g., 'red', or (255, 0, 0)).
        color2 (str or tuple): Color for the second polygon (e.g., 'blue', or (0, 0, 255)).

    Returns:
        bytes: Modified image as bytes.
    """
    # Load the image from bytes
    image = Image.open(BytesIO(image_bytes))

    # Create a drawable object
    draw = ImageDraw.Draw(image)

    # Draw the first polygon
    draw.line(polygon1 + [polygon1[0]], fill=color1, width=3)

    # Draw the second polygon
    draw.line(polygon2 + [polygon2[0]], fill=color2, width=3)

    # Save the modified image into a byte stream
    output_bytes_io = BytesIO()
    image.save(output_bytes_io, format="PNG")

    # Get the bytes of the modified image
    modified_image_bytes = output_bytes_io.getvalue()

    return modified_image_bytes


class PrefixStorage(Storage):

    def __init__(self, basePlugin: ScryptedDeviceBase, prefix: str):
        self.basePlugin = basePlugin
        self.prefix = prefix

    def getItem(self, key: str) -> str:
        return self.basePlugin.storage.getItem(f"{self.prefix}:{key}")

    def setItem(self, key: str, value: str):
        return self.basePlugin.storage.setItem(f"{self.prefix}:{key}", value)

    def removeItem(self, key: str):
        return self.basePlugin.storage.removeItem(f"{self.prefix}:{key}")

    def getKeys(self) -> AbstractSet[str]:
        keys = self.basePlugin.storage.getKeys()
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
    def __init__(self, reason: str, zone_bbox: shapely.geometry.Polygon = None, obj_bbox: shapely.geometry.Polygon = None):
        self.reason = reason
        self.zone_bbox = zone_bbox
        self.obj_bbox = obj_bbox


class NotificationFilterEditor(Camera):
    storage: Storage
    basePlugin: 'NotificationFilter'

    @property
    def selected_camera(self) -> list[str]:
        return self.storage.getItem("selected_camera")

    def zones_of(self, camera_id: str) -> list[str]:
        return self.storage.getItem(f"{camera_id}:zones") or []

    def zone_details_of(self, camera_id: str, zone: str) -> list[list[float]]:
        return self.storage.getItem(f"{camera_id}:zone:{zone}") or []

    def zone_type_of(self, camera_id: str, zone: str) -> str:
        return self.storage.getItem(f"{camera_id}:zone:{zone}:type") or "Intersect"

    async def get_all_detector_cameras(self) -> list[str]:
        detector_cameras = []
        state = scrypted_sdk.systemManager.getSystemState()
        for device_id in state.keys():
            device = self.get_device_from_scrypted(device_id)
            if not device:
                continue

            # prevent looping back to self
            if device.id in self.basePlugin.all_mixin_device_ids() or \
                device.id in self.basePlugin.all_preset_device_ids():
                continue

            if ScryptedInterface.Camera.value in device.interfaces and \
                ScryptedInterface.ObjectDetector.value in device.interfaces:
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
            ScryptedInterface.Camera.value in camera.interfaces and \
            ScryptedInterface.ObjectDetector.value in camera.interfaces

    def device_to_readable(self, device_id: str) -> str:
        device = self.get_device_from_scrypted(device_id)
        if not device:
            return None
        return f"{device.name} (id: {device.id})"

    def readable_to_device(self, readable: str) -> str:
        id = readable.split(" ")[-1]
        id = id.removeprefix("(id: ").removesuffix(")")
        return id

    async def editor_settings(self) -> list[Setting]:
        cameras = await self.get_all_detector_cameras()
        settings = [
            {
                "group": "Notification Zone Filter",
                "key": "selected_camera",
                "title": "Configure Zones for Camera",
                "description": "Select a camera to configure zones for.",
                "value": self.device_to_readable(self.selected_camera),
                "choices": [self.device_to_readable(camera_id) for camera_id in cameras],
                "immediate": True
            },
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

    async def getPictureOptions(self) -> list[ResponsePictureOptions]:
        camera: Camera | None = self.get_device_from_scrypted(self.selected_camera)
        if not camera:
            raise Exception("No camera selected")
        return await camera.getPictureOptions()

    async def takePicture(self, options: RequestPictureOptions = None) -> MediaObject:
        camera: Camera | None = self.get_device_from_scrypted(self.selected_camera)
        if not camera:
            raise Exception("No camera selected")
        return await camera.takePicture(options)


class NotificationFilterPreset(ScryptedDeviceBase, Settings, NotificationFilterEditor):

    def __init__(self, nativeId: str, basePlugin: 'NotificationFilter') -> None:
        super().__init__(nativeId)
        self.basePlugin = basePlugin

    async def getSettings(self) -> list[Setting]:
        return await self.editor_settings()

    async def putSetting(self, key: str, value: str) -> None:
        if key == "selected_camera":
            value = self.readable_to_device(value)
        self.storage.setItem(key, value)
        await self.onDeviceEvent(ScryptedInterface.Settings.value, None)


class NotificationFilterMixin(Notifier, Settings, NotificationFilterEditor):

    def __init__(self, basePlugin: 'NotificationFilter', mixinDevice: Any, mixinDeviceInterfaces: list[str], mixinDeviceState: WritableDeviceState):
        self.basePlugin = basePlugin
        self.mixinDevice = mixinDevice
        self.mixinDeviceInterfaces = mixinDeviceInterfaces
        self.mixinDeviceState = mixinDeviceState
        self.storage = PrefixStorage(basePlugin, f"mixin:{mixinDeviceState.id}")
        self.mixinConsole = getMixinConsole(mixinDeviceState.id, basePlugin)
        asyncio.create_task(reload_settings(mixinDeviceState.id, self))

    def debug_zones(self) -> bool:
        return self.storage.getItem("debug_zones") or False

    def use_custom(self) -> bool:
        return self.storage.getItem("use_custom") or False

    def selected_preset(self) -> str:
        # note that this is the scrypted device id, not the nativeId!
        return self.storage.getItem("selected_preset")

    async def sendNotification(self, title: str, options: NotifierOptions = None, media: str | MediaObject = None, icon: str | MediaObject = None) -> None:
        try:
            if not self.use_custom() and not self.selected_preset():
                raise ShouldSendNotification("no preset selected")

            if not self.use_custom() and not self.get_device_from_scrypted(self.selected_preset()):
                raise ShouldSendNotification("preset not found")

            if self.use_custom():
                preset = self
            else:
                preset = self.basePlugin.get_preset_by_scrypted_id(self.selected_preset())

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

            zones = preset.zones_of(device_id)
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
                    zone_details = preset.zone_details_of(device_id, zone)
                    if not zone_details:
                        continue

                    zone_details = [[x * inputDimensions[0], y * inputDimensions[1]] for [x, y] in zone_details]
                    zone_box = shapely.geometry.Polygon(zone_details)
                    no_zones_at_all = False

                    if preset.zone_type_of(device_id, zone) == "Intersect":
                        if detection_box.intersects(zone_box):
                            raise ShouldSendNotification(f"bounding box {detection_box} intersects zone {zone_box}", zone_box, detection_box)
                    else:
                        if detection_box.contains(zone_box):
                            raise ShouldSendNotification(f"bounding box {detection_box} contains zone {zone_box}", zone_box, detection_box)

            if no_zones_at_all:
                raise ShouldSendNotification("no detections or no zones")
        except ShouldSendNotification as e:
            await self.mixinConsole.info(f"Sending notification {title} because: {e.reason}")

            if self.debug_zones() and e.zone_bbox and e.obj_bbox:
                try:
                    device = scrypted_sdk.systemManager.getDeviceById(device_id)
                    image = await device.takePicture()
                    image_bytes = await scrypted_sdk.mediaManager.convertMediaObjectToBuffer(image, "image/png")

                    zone_bbox = [(x, y) for x, y in e.zone_bbox.exterior.coords]
                    obj_bbox = [(x, y) for x, y in e.obj_bbox.exterior.coords]
                    modified_image_bytes = draw_polygons_in_memory(image_bytes, zone_bbox, obj_bbox, 'red', 'blue')
                    media = await scrypted_sdk.mediaManager.createMediaObject(modified_image_bytes, "image/png")
                except Exception as e:
                    await self.mixinConsole.error(f"Failed to draw polygons: {e}")

            await self.mixinDevice.sendNotification(title, options, media, icon)
        except Exception as e:
            await self.mixinConsole.error(f"Failed to filter notification: {e}")
            await self.mixinDevice.sendNotification(title, options, media, icon)
        else:
            # nothing matched, so don't send
            await self.mixinConsole.info(f"Skipping notification: {title}")

    async def getSettings(self) -> list[Setting]:
        settings = []
        if ScryptedInterface.Settings.value in self.mixinDeviceInterfaces:
            settings.extend(await self.mixinDevice.getSettings())

        settings.append(
            {
                "group": "Notification Zone Filter",
                "key": "use_custom",
                "title": "Use Custom Zones",
                "description": "Enable to use custom zones for this notifier. Presets will not be used when enabled.",
                "type": "boolean",
                "value": self.use_custom()
            }
        )

        if self.use_custom():
            settings.extend(await self.editor_settings())
        else:
            presets = self.basePlugin.all_preset_devices()
            settings.append(
                {
                    "group": "Notification Zone Filter",
                    "key": "selected_preset",
                    "title": "Select Zone Filter Preset",
                    "description": "Select a preset to use for this notifier.",
                    "choices": [self.device_to_readable(preset.id) for preset in presets],
                    "value": self.device_to_readable(self.selected_preset())
                }
            )

        settings.append(
            {
                "group": "Notification Zone Filter",
                "key": "debug_zones",
                "title": "Debug Zones",
                "description": "Enable debug zones to send a full frame snapshot with the zone and object bounding boxes, replacing the original notification's image.",
                "type": "boolean",
                "value": self.debug_zones()
            }
        )

        return settings

    async def putSetting(self, key: str, value: str | list[str] | list[list[float]]) -> None:
        editor_settings = await self.editor_settings()
        my_keys = [setting["key"] for setting in editor_settings] + ["debug_zones", "use_custom", "selected_preset"]

        if key not in my_keys:
            await self.mixinDevice.putSetting(key, value)
            return

        if key == "selected_camera" or key == "selected_preset":
            value = self.readable_to_device(value)
        self.storage.setItem(key, value)
        await reload_settings(self.mixinDeviceState.id, self)


class NotificationFilter(ScryptedDeviceBase, MixinProvider, DeviceProvider, DeviceCreator):

    def __init__(self, nativeId: str | None = None):
        super().__init__(nativeId)

        # these use nativeIds as keys
        self.mixin_dict = {}
        self.preset_devices = {}

    def print(self, *args):
        print(*args)

    def all_mixin_device_ids(self) -> list[str]:
        return list(self.mixin_dict.keys())

    def all_preset_device_ids(self) -> list[str]:
        return list(self.preset_devices.keys())

    def all_preset_devices(self) -> list[NotificationFilterPreset]:
        return list(self.preset_devices.values())

    def get_preset_by_scrypted_id(self, device_id) -> NotificationFilterPreset:
        for preset in self.all_preset_devices():
            if preset.id == device_id:
                return preset
        return None

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
        # probably nothing to do here?
        return None

    async def createDevice(self, settings: DeviceCreatorSettings) -> str:
        nativeId = str(uuid.uuid4().hex)
        name = settings.get("name", "New Zone Filter Preset")
        await scrypted_sdk.deviceManager.onDeviceDiscovered({
            'nativeId': nativeId,
            'name': name,
            'interfaces': [
                ScryptedInterface.Camera.value,
                ScryptedInterface.Settings.value,
            ],
            'type': ScryptedDeviceType.API.value,
        })
        await self.getDevice(nativeId)
        return nativeId

    async def getCreateDeviceSettings(self) -> list[Setting]:
        return [
            {
                'title': 'Preset Name',
                'key': 'name',
            }
        ]

    async def releaseDevice(self, id: str, nativeId: str) -> None:
        if nativeId in self.preset_devices:
            del self.preset_devices[nativeId]

    async def getDevice(self, nativeId: str) -> Any:
        if nativeId not in self.preset_devices:
            self.preset_devices[nativeId] = NotificationFilterPreset(nativeId, self)
        return self.preset_devices[nativeId]


def create_scrypted_plugin():
    return NotificationFilter()