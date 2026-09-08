
# Sigma logsource category -> table name
LOGSOURCE_TABLE = {
    "process_creation":   ["process_events"],
    "process_access":     ["process_events"],
    "authentication":     ["auth_events"],
    "security":           ["auth_events"],
    "network_connection": ["network_events"],
    "firewall":           ["network_events"],
    "dns_query":          ["network_events"],
    "dns":                ["network_events"],
    "file_event":         ["file_events"],
    "file_change":        ["file_events"],
    "file_delete":        ["file_events"],
    "file_rename":        ["file_events"],
    "usb":                ["usb_events"],
    # one logsource, many engine tables — this is the case a flat dict broke:
    "database":           ["mysql_db_events", "postgres_db_events",
                           "redis_db_events", "oracle_db_events",
                           "mongo_db_events"],
}
# agent_name lives on every event row — no join needed.
HOST_COLUMN = "e.agent_name"

# Only for rules needing the parent process NAME (resolved via ppid self-join).
PARENT_JOIN = """JOIN process_events p
  ON p.agent_name = e.agent_name
 AND p.process_pid = e.process_ppid
 AND p.timestamp BETWEEN e.timestamp - INTERVAL '60 seconds' AND e.timestamp"""
REQUIRES_PARENT_JOIN = {"ParentImage", "ParentProcessName", "ParentCommandLine"}

# Sigma always filters on time; the column is `timestamp` (endpoint time).
# Do NOT use ingested_at — a delayed agent would hide attacks from lookbacks.
TIME_COLUMN = "e.timestamp"

# Actor column per table (used as the grouping "entity").
ENTITY_COLUMN = {
    "auth_events":    "e.auth_source_ip",
    "network_events": "e.network_src_ip",
    "process_events": "e.process_name",
    "file_events":    "e.user_name",
    "usb_events":     "e.usb_serial_number",
    "db_events":      "e.db_user",
}

FIELD_MAP = {
    "process_events": {
        # process_executable is the full path; COALESCE so Image rules still
        # match on name when the collector left executable NULL.
        "Image":              "COALESCE(e.process_executable, e.process_name)",
        "ProcessName":        "e.process_name",
        "NewProcessName":     "COALESCE(e.process_executable, e.process_name)",
        "OriginalFileName":   "e.process_name",
        "CommandLine":        "e.process_command_line",
        "ProcessCommandLine": "e.process_command_line",
        "CurrentDirectory":   "e.process_working_dir",
        "User":               "e.process_user",
        "ProcessId":          "e.process_pid",
        "ParentProcessId":    "e.process_ppid",
        "Hashes":             "e.process_sha256",
        "SHA256":             "e.process_sha256",
        "Computer":           HOST_COLUMN,
        "Hostname":           HOST_COLUMN,
        # need PARENT_JOIN
        "ParentImage":        "COALESCE(p.process_executable, p.process_name)",
        "ParentProcessName":  "p.process_name",
        "ParentCommandLine":  "p.process_command_line",
    },
    "auth_events": {
        "TargetUserName":     "e.username",
        "SubjectUserName":    "e.username",
        "User":               "e.username",
        "AccountName":        "e.username",
        "IpAddress":          "e.auth_source_ip",
        "SourceIp":           "e.auth_source_ip",
        "SourceAddress":      "e.auth_source_ip",
        "SourcePort":         "e.auth_source_port",
        "LogonType":          "e.auth_session_type",
        "AuthenticationPackageName": "e.auth_method",
        "Status":             "e.outcome",
        "FailureReason":      "e.auth_failure_reason",
        "ProcessName":        "e.process_name",
        "ProcessId":          "e.process_pid",
        "Computer":           HOST_COLUMN,
        "WorkstationName":    HOST_COLUMN,
        "CommandLine":        "e.auth_sudo_command",
        "TerminalSessionId":  "e.user_terminal",
    },
    "network_events": {
        "SourceIp":           "e.network_src_ip",
        "src_ip":             "e.network_src_ip",
        "SourcePort":         "e.network_src_port",
        "src_port":           "e.network_src_port",
        "DestinationIp":      "e.network_dst_ip",
        "dst_ip":             "e.network_dst_ip",
        "DestinationPort":    "e.network_dst_port",
        "dst_port":           "e.network_dst_port",
        "Protocol":           "e.network_transport",
        "Initiated":          "e.network_direction",
        "Image":              "COALESCE(e.process_executable, e.process_name)",
        "ProcessName":        "e.process_name",
        "CommandLine":        "e.process_command_line",
        "User":               "e.process_user",
        "query":              "e.network_dns_query",
        "QueryName":          "e.network_dns_query",
        "answer":             "e.network_dns_response",
        "Computer":           HOST_COLUMN,
    },
    "file_events": {
        "TargetFilename":     "e.file_path",
        "FileName":           "e.file_name",
        "Filename":           "e.file_name",
        "SourceFilename":     "e.file_old_path",
        "TargetPath":         "e.file_directory",
        "Extension":          "e.file_extension",
        "Hashes":             "e.file_sha256",
        "SHA256":             "e.file_sha256",
        "MD5":                "e.file_md5",
        "User":               "e.user_name",
        "Computer":           HOST_COLUMN,
    },
    "usb_events": {
        "DeviceSerial":       "e.usb_serial_number",
        "SerialNumber":       "e.usb_serial_number",
        "Vendor":             "e.usb_vendor",
        "Model":              "e.usb_model",
        "DeviceName":         "e.usb_label",
        "TargetFilename":     "e.file_path",
        "Computer":           HOST_COLUMN,
    },
}

# Fields common Sigma rules want but this schema does not collect — each is a
# data-collection gap, not a mapping bug.
KNOWN_GAPS = {
    "process_events": ["IntegrityLevel", "LogonId", "imphash", "Company",
                       "Product", "Description", "OriginalFileName"],
    "auth_events":    ["EventID", "TargetDomainName", "LogonProcessName"],
    "registry":       ["ALL — no registry_events table"],
    "image_load":     ["ALL — image loads not collected"],
    "ps_script":      ["ALL — PowerShell script-block logging not collected"],
}
