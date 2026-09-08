import os
import glob
from typing import Any, Dict, List, Optional

import yaml
from .sigma_fieldmap import (LOGSOURCE_TABLE, FIELD_MAP,
    PARENT_JOIN, REQUIRES_PARENT_JOIN, TIME_COLUMN, ENTITY_COLUMN)

# ── Sigma logsource -> your table ───────────────────
# Sigma categorises rules by logsource (category/product/service). Map each to
# whichever of your six tables holds that data.
_OLD_LOGSOURCE = {
    "process_creation":       "process_logs",
    "authentication":         "auth_logs",
    "network_connection":     "network_logs",
    "firewall":               "network_logs",
    "file_event":             "file_logs",
    "file_change":            "file_logs",
    "file_delete":            "file_logs",
    "usb":                    "usb_logs",
    "database":               "db_logs",
    # Windows security channel rules mostly describe auth activity.
    "security":               "auth_logs",
}

# ── Sigma field name -> your column name, per table ─
# Extend this as you adopt more rules. Anything unmapped is reported rather
# than silently dropped, so you always know what a rule could not express.
_OLD_FIELD_MAP = {
    "process_logs": {
        "Image": "process_name",
        "ProcessName": "process_name",
        "NewProcessName": "process_name",
        "CommandLine": "command_line",
        "ProcessCommandLine": "command_line",
        "ParentImage": "parent_process",
        "ParentProcessName": "parent_process",
        "ParentCommandLine": "parent_process",
        "User": "user_name",
        "SubjectUserName": "user_name",
        "TargetUserName": "user_name",
        "Computer": "host_name",
        "ProcessId": "pid",
    },
    "auth_logs": {
        "TargetUserName": "user_name",
        "SubjectUserName": "user_name",
        "User": "user_name",
        "IpAddress": "source_ip",
        "SourceIp": "source_ip",
        "SourceAddress": "source_ip",
        "Computer": "host_name",
        "WorkstationName": "host_name",
        "LogonType": "auth_method",
        "AuthenticationPackageName": "auth_method",
        "Status": "outcome",
    },
    "network_logs": {
        "SourceIp": "source_ip",
        "src_ip": "source_ip",
        "DestinationIp": "dest_ip",
        "dst_ip": "dest_ip",
        "DestinationPort": "dest_port",
        "dst_port": "dest_port",
        "Protocol": "protocol",
        "Initiated": "direction",
        "Computer": "host_name",
    },
    "file_logs": {
        "TargetFilename": "file_path",
        "FileName": "file_path",
        "Image": "process_name",
        "User": "user_name",
        "Computer": "host_name",
    },
    "usb_logs": {
        "DeviceSerial": "device_serial",
        "Vendor": "vendor",
        "User": "user_name",
        "Computer": "host_name",
    },
    "db_logs": {
        "User": "db_user",
        "DatabaseUser": "db_user",
        "Query": "query_text",
        "SourceIp": "source_ip",
        "Computer": "host_name",
    },
}

SEVERITY_MAP = {"informational": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}


class UnmappableRule(Exception):
    """Raised when a rule cannot be expressed against the configured schema."""


class SigmaRule:
    """One parsed Sigma rule, compiled to a Postgres WHERE clause."""

    def __init__(self, doc: Dict[str, Any], path: str = ""):
        self.doc = doc
        self.path = path
        self.id = doc.get("id", "")
        self.title = doc.get("title", "untitled")
        self.description = doc.get("description", "")
        self.level = doc.get("level", "medium")
        self.severity = SEVERITY_MAP.get(str(self.level).lower(), 3)
        self.tags = doc.get("tags", []) or []
        self.author = doc.get("author", "")
        self.unmapped_fields: List[str] = []
        self.needs_parent_join = False

        ls = doc.get("logsource", {}) or {}
        key = ls.get("category") or ls.get("service") or ls.get("product") or ""
        self.logsource = key
        tables = LOGSOURCE_TABLE.get(key)
        if not tables:
            raise UnmappableRule(f"logsource '{key}' not mapped to a table")
        # values are always lists now. One logsource may target several tables
        # (e.g. "database" -> all engine tables). They share the same field
        # schema, so we compile the WHERE once against the first (primary) table
        # and reuse it for the rest; to_sql() emits one query per table.
        if isinstance(tables, str):          # tolerate an old string value
            tables = [tables]
        self.tables = tables
        self.table = tables[0]               # primary, drives field mapping

        self.fieldmap = FIELD_MAP.get(self.table, {})
        self.where, self.params = self._compile_detection(doc.get("detection", {}))

    # ── detection block -> SQL ──────────────────────
    def _compile_detection(self, detection: Dict[str, Any]):
        if not detection:
            raise UnmappableRule("no detection block")
        condition = detection.get("condition", "")
        if not isinstance(condition, str):
            condition = str(condition)

        # Supported: "selection", "selection and not filter", "sel1 or sel2",
        # "all of selection*", "1 of selection*". Anything more exotic (near,
        # aggregations, count()) is rejected rather than mis-translated.
        low = condition.lower()
        for unsupported in ("| count", "|count", " near ", "aggregation"):
            if unsupported in low:
                raise UnmappableRule(f"unsupported condition: {condition}")

        params: Dict[str, Any] = {}
        counter = [0]

        def build_block(name: str) -> str:
            block = detection.get(name)
            if block is None:
                raise UnmappableRule(f"condition references unknown block '{name}'")
            return self._block_sql(block, params, counter)

        # Expand wildcard selectors like "selection*".
        def expand(token: str) -> List[str]:
            if token.endswith("*"):
                prefix = token[:-1]
                return [k for k in detection if k != "condition" and k.startswith(prefix)]
            return [token]

        parts = low.replace("(", " ( ").replace(")", " ) ").split()
        sql_tokens: List[str] = []
        i = 0
        while i < len(parts):
            tok = parts[i]
            if tok in ("and", "or"):
                sql_tokens.append(tok.upper())
            elif tok == "not":
                sql_tokens.append("NOT")
            elif tok in ("(", ")"):
                sql_tokens.append(tok)
            elif tok in ("all", "1") and i + 2 < len(parts) and parts[i + 1] == "of":
                names = expand(parts[i + 2])
                if not names:
                    raise UnmappableRule(f"no blocks match '{parts[i+2]}'")
                joiner = " AND " if tok == "all" else " OR "
                sql_tokens.append("(" + joiner.join(build_block(n) for n in names) + ")")
                i += 2
            elif tok == "them":
                names = [k for k in detection if k != "condition"]
                sql_tokens.append("(" + " OR ".join(build_block(n) for n in names) + ")")
            else:
                # a named selection/filter block, possibly with a wildcard
                names = expand(tok)
                if len(names) == 1:
                    sql_tokens.append(build_block(names[0]))
                else:
                    sql_tokens.append("(" + " OR ".join(build_block(n) for n in names) + ")")
            i += 1

        where = " ".join(sql_tokens).strip()
        if not where:
            raise UnmappableRule("empty condition")
        return where, params

    def _block_sql(self, block, params, counter) -> str:
        """A selection block: dict of field->value, or list of such dicts."""
        if isinstance(block, list):
            return "(" + " OR ".join(
                self._block_sql(b, params, counter) for b in block) + ")"
        if not isinstance(block, dict):
            raise UnmappableRule("unsupported selection block type")

        clauses = []
        for raw_field, value in block.items():
            field, modifiers = self._split_modifiers(raw_field)
            if field in REQUIRES_PARENT_JOIN:
                self.needs_parent_join = True
            column = self.fieldmap.get(field)
            if not column:
                # Track it so the operator can extend FIELD_MAP knowingly.
                self.unmapped_fields.append(field)
                raise UnmappableRule(f"field '{field}' not mapped for {self.table}")
            clauses.append(self._value_sql(column, value, modifiers, params, counter))
        return "(" + " AND ".join(clauses) + ")"

    @staticmethod
    def _split_modifiers(raw: str):
        parts = raw.split("|")
        return parts[0], [p.lower() for p in parts[1:]]

    def _value_sql(self, column, value, modifiers, params, counter) -> str:
        """Render one field comparison, honouring Sigma value modifiers."""
        if isinstance(value, list):
            return "(" + " OR ".join(
                self._value_sql(column, v, modifiers, params, counter)
                for v in value) + ")"

        counter[0] += 1
        key = f"p{counter[0]}"

        if value is None:
            return f"{column} IS NULL"

        # Sigma booleans (e.g. "Initiated: true" = outbound). Our columns are
        # text, not boolean, so translate rather than emit `col = true`.
        if isinstance(value, bool):
            return self._bool_sql(column, value, params, key)

        sval = str(value)

        if "contains" in modifiers:
            params[key] = f"%{self._esc(sval)}%"
            return f"{column} ILIKE %({key})s"
        if "startswith" in modifiers:
            params[key] = f"{self._esc(sval)}%"
            return f"{column} ILIKE %({key})s"
        if "endswith" in modifiers:
            params[key] = f"%{self._esc(sval)}"
            return f"{column} ILIKE %({key})s"
        if "re" in modifiers:
            # Sigma regex is PCRE; Postgres is POSIX. Sanitise the differences
            # (repetition cap, inline flags) or the query fails at execution.
            params[key] = self._posix_regex(sval)
            return f"{column} ~ %({key})s"
        if "gt" in modifiers:
            params[key] = value
            return f"{column} > %({key})s"
        if "lt" in modifiers:
            params[key] = value
            return f"{column} < %({key})s"

        # Plain value: Sigma treats * and ? as wildcards.
        if "*" in sval or "?" in sval:
            params[key] = self._esc(sval).replace("*", "%").replace("?", "_")
            return f"{column} ILIKE %({key})s"

        params[key] = value
        return f"{column} = %({key})s"

    # Text columns that encode a Sigma boolean. Maps the True meaning; False
    # negates it. Extend if you map more boolean-valued Sigma fields.
    _BOOL_COLUMNS = {
        "e.network_direction": ("outbound", "inbound"),  # Initiated: true = outbound
    }

    def _bool_sql(self, column, value, params, key) -> str:
        mapping = self._BOOL_COLUMNS.get(column)
        if mapping:
            true_val, false_val = mapping
            params[key] = true_val if value else false_val
            return f"{column} = %({key})s"
        # Unknown boolean field mapped to a real boolean column, or NULL check.
        if value:
            return f"{column} IS NOT NULL"
        return f"{column} IS NULL"

    @staticmethod
    def _posix_regex(pattern: str) -> str:
        """Make a PCRE Sigma regex safe for Postgres POSIX matching.

        Two incompatibilities bite in practice:
          • POSIX caps bounded repetition at 255; Sigma rules use {1,256} etc.
            Rewrite any bound >255 down to 255 (semantics: 'a long run').
          • PCRE non-capturing groups (?:...) and inline flags (?i) are accepted
            by Postgres, so those are left alone.
        """
        import re as _re

        def clamp(m):
            lo, hi = m.group(1), m.group(2)
            if hi and hi.isdigit() and int(hi) > 255:
                hi = "255"
            if lo and lo.isdigit() and int(lo) > 255:
                lo = "255"
            return "{" + lo + ("," + hi if m.group(2) is not None else "") + "}"

        # match {n} or {n,} or {n,m}
        return _re.sub(r"\{(\d+)(?:,(\d*))?\}",
                       lambda m: clamp(m) if m.group(2) is not None or
                       (m.group(1).isdigit() and int(m.group(1)) > 255)
                       else m.group(0),
                       pattern)

    @staticmethod
    def _esc(s: str) -> str:
        """Escape LIKE metacharacters that came from literal rule text."""
        return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # ── full query ──────────────────────────────────
    def _sql_for_table(self, table: str, lookback: str) -> str:
        joins = PARENT_JOIN if self.needs_parent_join else ""
        entity = ENTITY_COLUMN.get(table, "e.agent_name")
        return f"""
SELECT %(rule_id)s AS rule_id,
       %(severity)s AS severity,
       e.agent_name ,
       {entity} AS entity,
       count(*) AS event_count,
       min({TIME_COLUMN}) AS first_seen,
       max({TIME_COLUMN}) AS last_seen,
       %(title)s AS detail
FROM {table} e
{joins}
WHERE {TIME_COLUMN} > now() - INTERVAL '{lookback}'
  AND ({self.where})
GROUP BY e.agent_name, {entity}
"""

    def to_sql(self, lookback: str = "24 hours") -> str:
        """Query for the PRIMARY table (back-compatible: callers that expect a
        single string still work)."""
        return self._sql_for_table(self.table, lookback)

    def to_sql_all(self, lookback: str = "24 hours") -> List[str]:
        """One query per mapped table. For single-table rules this is a 1-item
        list; for multi-table logsources (e.g. database) it's one per engine."""
        return [self._sql_for_table(t, lookback) for t in self.tables]


    def _entity_column(self) -> str:
        """Whichever column best identifies the actor, per table."""
        return {
            "auth_logs": "host(source_ip)",
            "network_logs": "host(source_ip)",
            "process_logs": "process_name",
            "file_logs": "process_name",
            "usb_logs": "device_serial",
            "db_logs": "db_user",
        }.get(self.table, "host_name")

    def query_params(self) -> Dict[str, Any]:
        return dict(self.params,
                    rule_id=f"SIGMA_{(self.id or self.title)[:40]}",
                    severity=self.severity,
                    title=self.title[:200])


# ── loading ─────────────────────────────────────────
def load_rules(path: str) -> Dict[str, Any]:
    """Load every .yml under a path. Returns compiled rules plus skip reasons.

    Public Sigma repos hold thousands of rules for products you may not run;
    skipping is normal and expected. The skipped list tells you what you would
    gain by extending FIELD_MAP / LOGSOURCE_TABLE.
    """
    files = []
    if os.path.isfile(path):
        files = [path]
    else:
        files = sorted(glob.glob(os.path.join(path, "**", "*.yml"), recursive=True))

    compiled, skipped = [], []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                docs = [d for d in yaml.safe_load_all(fh) if isinstance(d, dict)]
        except Exception as e:
            skipped.append((f, f"yaml error: {str(e)[:80]}"))
            continue
        for doc in docs:
            if "detection" not in doc:
                continue
            try:
                compiled.append(SigmaRule(doc, f))
            except UnmappableRule as e:
                skipped.append((doc.get("title", os.path.basename(f)), str(e)))
            except Exception as e:
                skipped.append((doc.get("title", os.path.basename(f)),
                                f"error: {str(e)[:80]}"))
    return {"rules": compiled, "skipped": skipped,
            "loaded": len(compiled), "skipped_count": len(skipped)}