"""Processing algorithm: Smart name join (rules + optional AI)."""
import os

from qgis.core import (QgsApplication, QgsAuthMethodConfig, QgsFeature, QgsFeatureRequest, 
                       QgsField, QgsFields, QgsProcessingAlgorithm, QgsProcessingException,
                       QgsProcessingParameterAuthConfig, QgsProcessingParameterBoolean, QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterField, QgsProcessingParameterNumber, QgsProcessingParameterString,)
from qgis.PyQt.QtCore import QCoreApplication

from . import engine, llm
from .compat import NO_GEOMETRY, FIELD_ANY, FIELD_STRING_T, FIELD_DOUBLE_T, NUM_DOUBLE, NUM_INT, SRC_ANY, SRC_VECTOR, sink_flag, ptype

PROVIDERS = [("none", "No AI - free, rules only"), ("ollama", "Ollama (local, free)"),
             ("openai", "OpenAI-compatible API (OpenAI, OpenRouter, LM Studio...)"),
             ("anthropic", "Claude API (Anthropic key)"), ("claude_cli", "Claude Code CLI (your Claude plan)")]

MATCH_FIELDS = [("match_status", FIELD_STRING_T), ("match_method", FIELD_STRING_T), ("match_score", FIELD_DOUBLE_T),
                ("ai_confidence", FIELD_DOUBLE_T), ("match_reason", FIELD_STRING_T), ("matched_name", FIELD_STRING_T),
                ("matched_group", FIELD_STRING_T), ("alternatives", FIELD_STRING_T)]


def tr(s):
    return QCoreApplication.translate("SmartNameJoin", s)


class SmartNameJoinAlgorithm(QgsProcessingAlgorithm):
    TABLE, TNAME, TGROUP, TINFO = "TABLE", "TABLE_NAME", "TABLE_GROUP", "TABLE_INFO"
    LAYER, LNAME, LGROUP, LINFO, LCOPY = "LAYER", "LAYER_NAME", "LAYER_GROUP", "LAYER_INFO", "LAYER_FIELDS"
    PROVIDER, MODEL, URL, AUTH, CONTEXT = "AI_PROVIDER", "AI_MODEL", "AI_URL", "AI_AUTH", "CONTEXT"
    ACCEPT, MINCONF, BATCH, UNMATCHED, OUTPUT = "AUTO_ACCEPT", "AI_MIN_CONFIDENCE", "BATCH", "KEEP_UNMATCHED", "OUTPUT"

    def name(self):
        return "smartnamejoin"

    def displayName(self):
        return tr("Smart name join (fuzzy + AI)")

    def group(self):
        return ""

    def groupId(self):
        return ""

    def shortHelpString(self):
        return tr(
            "Joins a table to a layer by NAME when the spellings do not match "
            "(Tiruvallur / Thiruvallur, Ooty / Udhagamandalam, 'Chengalpattu district' / Chengalpattu).\n\n"
            "Steps: exact -> normalized (accents, punctuation, words like 'district') -> transliteration key -> "
            "fuzzy score -> optional AI. The AI only chooses among real features of your layer, so it cannot "
            "invent a match. Every output row says HOW it was matched and why, and rows that need a human "
            "are marked match_status = review.\n\n"
            "Optional group fields (e.g. district) make the search faster and safer; group names are matched "
            "the same way. With no AI the tool is free and runs offline.")

    def createInstance(self):
        return SmartNameJoinAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(self.TABLE, tr("Table to join (the messy names)"), [SRC_ANY]))
        self.addParameter(QgsProcessingParameterField(self.TNAME, tr("Table: name field"), parentLayerParameterName=self.TABLE, type=FIELD_ANY))
        self.addParameter(QgsProcessingParameterField(self.TGROUP, tr("Table: group field, e.g. district (optional)"),
                                                      parentLayerParameterName=self.TABLE, type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(self.TINFO, tr("Table: extra info fields shown to the AI (optional)"),
                                                      parentLayerParameterName=self.TABLE, type=FIELD_ANY, allowMultiple=True, optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(self.LAYER, tr("Layer to join to (the map)"), [SRC_VECTOR]))
        self.addParameter(QgsProcessingParameterField(self.LNAME, tr("Layer: name field"), parentLayerParameterName=self.LAYER, type=FIELD_ANY))
        self.addParameter(QgsProcessingParameterField(self.LGROUP, tr("Layer: group field (optional)"),
                                                      parentLayerParameterName=self.LAYER, type=FIELD_ANY, optional=True))
        self.addParameter(QgsProcessingParameterField(self.LINFO, tr("Layer: extra info fields shown to the AI (optional)"),
                                                      parentLayerParameterName=self.LAYER, type=FIELD_ANY, allowMultiple=True, optional=True))
        self.addParameter(QgsProcessingParameterField(self.LCOPY, tr("Layer fields to copy into the result (optional)"),
                                                      parentLayerParameterName=self.LAYER, type=FIELD_ANY, allowMultiple=True, optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.PROVIDER, tr("AI for the hard rows"), [p[1] for p in PROVIDERS], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(self.MODEL, tr("AI model (blank = default)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.URL, tr("AI server URL (Ollama / OpenAI-compatible; blank = default)"), optional=True))
        self.addParameter(QgsProcessingParameterAuthConfig(self.AUTH, tr("API key (QGIS authentication config, Basic: key in password)"), optional=True))
        self.addParameter(QgsProcessingParameterString(self.CONTEXT, tr("Context for the AI, e.g. 'Towns in Tamil Nadu, India'"), optional=True))
        adv = [QgsProcessingParameterNumber(self.ACCEPT, tr("Auto-accept fuzzy score"), NUM_DOUBLE, 0.92, False, 0.5, 1.0),
               QgsProcessingParameterNumber(self.MINCONF, tr("Minimum AI confidence"), NUM_DOUBLE, 0.6, False, 0.0, 1.0),
               QgsProcessingParameterNumber(self.BATCH, tr("Rows per AI request"), NUM_INT, 20, False, 1, 100),
               QgsProcessingParameterBoolean(self.UNMATCHED, tr("Keep unmatched rows (no geometry)"), True)]
        for p in adv:
            p.setFlags(p.flags() | ptype.ADVANCED)
            self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, tr("Joined"), SRC_VECTOR))

    def _fields(self, parameters, name, context):
        f = getattr(self, "parameterAsStrings", None) or self.parameterAsFields     # 3.40+ / older
        return f(parameters, name, context)

    def _key(self, parameters, context):
        authid = self.parameterAsString(parameters, self.AUTH, context)
        if not authid:
            return ""
        cfg = QgsAuthMethodConfig()
        QgsApplication.authManager().loadAuthenticationConfig(authid, cfg, True)
        conf = cfg.configMap()
        for k in ("password", "token", "key", "apikey", "api_key"):
            if conf.get(k):
                return conf[k]
        return next((v for v in conf.values() if v), "")

    def processAlgorithm(self, parameters, context, feedback):
        table = self.parameterAsSource(parameters, self.TABLE, context)
        layer = self.parameterAsSource(parameters, self.LAYER, context)
        if table is None or layer is None:
            raise QgsProcessingException(tr("Invalid input"))
        tname = self.parameterAsString(parameters, self.TNAME, context)
        tgroup = self.parameterAsString(parameters, self.TGROUP, context)
        tinfo = self._fields(parameters, self.TINFO, context)
        lname = self.parameterAsString(parameters, self.LNAME, context)
        lgroup = self.parameterAsString(parameters, self.LGROUP, context)
        linfo = self._fields(parameters, self.LINFO, context)
        lcopy = self._fields(parameters, self.LCOPY, context)
        provider = PROVIDERS[self.parameterAsEnum(parameters, self.PROVIDER, context)][0]

        def val(f, name):
            v = f[name] if name else None
            return None if v is None or (hasattr(v, "isNull") and v.isNull()) else str(v)

        feedback.pushInfo(tr("Reading layer..."))
        targets, fids = [], []
        for f in layer.getFeatures(QgsFeatureRequest().setFlags(NO_GEOMETRY)):
            n = val(f, lname)
            if not n:
                continue
            targets.append({"id": f.id(), "name": n, "group": val(f, lgroup),
                            "extra": ", ".join(f"{x}: {val(f, x)}" for x in linfo if val(f, x))})
        rows, tfeats = [], []
        for f in table.getFeatures():
            tfeats.append(f)
            rows.append({"key": f.id(), "name": val(f, tname), "group": val(f, tgroup),
                         "info": ", ".join(f"{x}: {val(f, x)}" for x in tinfo if val(f, x))})
        feedback.pushInfo(tr(f"{len(rows)} table rows, {len(targets)} layer features"))

        client = None
        if provider != "none":
            cache = os.path.join(QgsApplication.qgisSettingsDirPath(), "smart_name_join", "ai_cache.json")
            client = llm.CachedLLM(provider, self.parameterAsString(parameters, self.MODEL, context).strip(),
                                   self.parameterAsString(parameters, self.URL, context).strip(),
                                   self._key(parameters, context), cache_path=cache, log=feedback.pushInfo)
        results, summary = engine.run(
            rows, targets, llm=client, context=self.parameterAsString(parameters, self.CONTEXT, context),
            auto_accept=self.parameterAsDouble(parameters, self.ACCEPT, context),
            ai_min_conf=self.parameterAsDouble(parameters, self.MINCONF, context),
            batch=self.parameterAsInt(parameters, self.BATCH, context),
            use_groups=bool(tgroup and lgroup), log=feedback.pushInfo, feedback=feedback)

        fields = QgsFields(table.fields())
        for n, t in MATCH_FIELDS:
            fields.append(QgsField(n, t))
        lf = layer.fields()
        for n in lcopy:
            src = lf.field(n)
            fields.append(QgsField("layer_" + n, src.type(), src.typeName(), src.length(), src.precision()))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, layer.wkbType(), layer.sourceCrs())
        if sink is None:
            raise QgsProcessingException(tr("Cannot create output"))

        need = {r["target_id"] for r in results if r["target_id"] is not None}
        geo = {f.id(): f for f in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(need)))} if need else {}
        keep = self.parameterAsBool(parameters, self.UNMATCHED, context)
        for f, r in zip(tfeats, results):
            if r["target_id"] is None and not keep:
                continue
            o = QgsFeature(fields)
            attrs = list(f.attributes()) + [r["status"], r["method"], r["score"], r["ai_confidence"], r["reason"],
                                            r["target_name"], r["target_group"], r["alternatives"]]
            g = geo.get(r["target_id"])
            attrs += [g[n] if g is not None else None for n in lcopy]
            o.setAttributes(attrs)
            if g is not None:
                o.setGeometry(g.geometry())
            sink.addFeature(o, sink_flag())
        msg = ", ".join(f"{k}: {v}" for k, v in summary.items() if k != "group_map")
        feedback.pushInfo(tr("Result -> ") + msg)
        return {self.OUTPUT: dest, "MATCHED": summary.get("matched", 0), "REVIEW": summary.get("review", 0),
                "UNMATCHED": summary.get("none", 0)}
