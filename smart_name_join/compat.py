"""Enum names moved between QGIS 3.28 and 4.x; resolve them once here."""
from qgis.core import Qgis, QgsFeatureSink, QgsProcessing, QgsProcessingParameterDefinition, QgsProcessingParameterField, \
    QgsProcessingParameterNumber

try:
    FIELD_ANY = Qgis.ProcessingFieldParameterDataType.Any
except AttributeError:
    FIELD_ANY = QgsProcessingParameterField.Any
try:
    SRC_ANY = Qgis.ProcessingSourceType.Vector                 # with or without geometry (plain tables too)
    SRC_VECTOR = Qgis.ProcessingSourceType.VectorAnyGeometry
except AttributeError:
    SRC_ANY = QgsProcessing.TypeVector
    SRC_VECTOR = QgsProcessing.TypeVectorAnyGeometry
try:
    NUM_DOUBLE = Qgis.ProcessingNumberParameterType.Double
    NUM_INT = Qgis.ProcessingNumberParameterType.Integer
except AttributeError:
    NUM_DOUBLE = QgsProcessingParameterNumber.Double
    NUM_INT = QgsProcessingParameterNumber.Integer


class ptype:  # noqa: N801
    try:
        ADVANCED = Qgis.ProcessingParameterFlag.Advanced
    except AttributeError:
        ADVANCED = QgsProcessingParameterDefinition.FlagAdvanced


if Qgis.versionInt() >= 33800:            # QgsField(name, QMetaType) arrived in 3.38; required on Qt6 builds
    from qgis.PyQt.QtCore import QMetaType
    FIELD_STRING_T = QMetaType.Type.QString
    FIELD_DOUBLE_T = QMetaType.Type.Double
else:
    from qgis.PyQt.QtCore import QVariant
    FIELD_STRING_T = QVariant.String
    FIELD_DOUBLE_T = QVariant.Double


def sink_flag():
    try:
        return QgsFeatureSink.Flag.FastInsert
    except AttributeError:
        return QgsFeatureSink.FastInsert


try:
    NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry
except AttributeError:
    from qgis.core import QgsFeatureRequest
    NO_GEOMETRY = QgsFeatureRequest.NoGeometry
