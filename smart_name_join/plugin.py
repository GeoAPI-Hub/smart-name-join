import os

from qgis.core import QgsApplication, QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon

from .algorithm import SmartNameJoinAlgorithm

HERE = os.path.dirname(__file__)
ICON = os.path.join(HERE, "icon.svg")


class Provider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(SmartNameJoinAlgorithm())

    def id(self):
        return "smartnamejoin"

    def name(self):
        return "Smart Name Join"

    def icon(self):
        return QIcon(ICON)


class SmartNameJoinPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None

    def initProcessing(self):  # noqa: N802
        self.provider = Provider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):  # noqa: N802
        self.initProcessing()
        try:
            from qgis.PyQt.QtGui import QAction      # Qt6 (QGIS 4)
        except ImportError:
            from qgis.PyQt.QtWidgets import QAction  # Qt5 (QGIS 3)
        self.action = QAction(QIcon(ICON), "Smart Name Join (fuzzy + AI)...", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToVectorMenu("Smart Name Join", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginVectorMenu("Smart Name Join", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)

    def run(self):
        import processing
        processing.execAlgorithmDialog("smartnamejoin:smartnamejoin")
