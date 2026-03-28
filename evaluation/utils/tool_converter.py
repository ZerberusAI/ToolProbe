"""
Tool Parameter Converter Utility

Normalizes tool parameters from various formats to a consistent internal format.
Handles both MCP JSON Schema format and the simplified flat format.
"""


def normalize_parameters(parameters: dict) -> dict:
    """
    Normalize tool parameters to flat format for internal processing.

    Handles both formats:
    - Flat format: {"param": {"type": "string", "required": true}}
    - JSON Schema format: {"type": "object", "properties": {"param": {...}}, "required": ["param"]}

    Args:
        parameters: Tool parameters in either format

    Returns:
        Parameters in flat format: {"param": {"type": "string", "required": true, ...}}
    """
    if not parameters:
        return {}

    # Detect JSON Schema format by checking for "type": "object" with "properties"
    if parameters.get("type") == "object" and "properties" in parameters:
        flat = {}
        required_list = parameters.get("required", [])

        for name, spec in parameters.get("properties", {}).items():
            # Copy spec and add required flag
            flat[name] = {**spec}
            flat[name]["required"] = name in required_list

        return flat

    # Already in flat format or empty - return as-is
    return parameters
