"""Smart Name Join - join a table to a layer when the names are spelled differently."""


def classFactory(iface):  # noqa: N802 (QGIS API name)
    from .plugin import SmartNameJoinPlugin
    return SmartNameJoinPlugin(iface)
