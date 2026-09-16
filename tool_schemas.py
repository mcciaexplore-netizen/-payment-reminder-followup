"""Optional read-only tool definitions. Models have no approval, send or log tools."""
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_invoices", "description": "Read and validate an invoice file.",
        "parameters": {"type": "object", "properties": {"xlsx_path": {"type": "string"}}, "required": ["xlsx_path"]},
    }},
    {"type": "function", "function": {
        "name": "filter_overdue", "description": "Select validated unpaid invoices past the overdue threshold.",
        "parameters": {"type": "object", "properties": {
            "invoices_json": {"type": "string"}, "days_threshold": {"type": "integer", "minimum": 1, "maximum": 365}
        }, "required": ["invoices_json"]},
    }},
]
