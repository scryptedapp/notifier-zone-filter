# Notifier Zone Filter

The Notifier Zone Filter provides the ability to define object detection zones for notifiers,
allowing users to filter out unwanted notifications for objects detected outside of the
configured zones. The zone definitions and filters are equivalent to those provided by
object detection plugins; using this plugin's zones in lieu of the object detection zones
allows such detection plugins to continue registering events internally (e.g. for NVR clip
recording) without delivering a notification.

To get started, create a new device under this plugin to define a zone filter preset.
In the preset settings, select a camera from the dropdown to edit the zones for that camera.
Enable this plugin as an extension on the desired notifier device (i.e. an NVR push notifier),
then enable the preset. Alternatively, custom zones can be defined on each notifier individually
by enabling custom zones, selecting a camera, and editing the zones for that camera.