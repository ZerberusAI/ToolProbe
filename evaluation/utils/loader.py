"""
Datapoint Loader

Utilities for loading MCP safety evaluation datapoints.
"""
import json
from pathlib import Path


def load_all_datapoints(base_path: Path | str) -> list[dict]:
    """
    Load all datapoints from the base path.

    Searches for MCP-*.json files in subdirectories.

    Args:
        base_path: Path to the datasets/tool-calling directory

    Returns:
        List of datapoint dictionaries
    """
    base_path = Path(base_path)
    datapoints = []

    # Look for MCP-*.json files in datapoints subdirectories
    for json_file in base_path.glob("datapoints/*/MCP-*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                dp = json.load(f)
                datapoints.append(dp)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Failed to load {json_file}: {e}")

    # Sort by datapoint_id for consistent ordering
    datapoints.sort(key=lambda x: x.get("datapoint_id", ""))

    return datapoints


def load_datapoint_by_id(base_path: Path | str, datapoint_id: str) -> dict | None:
    """
    Load a specific datapoint by ID.

    Args:
        base_path: Path to the datasets/tool-calling directory
        datapoint_id: ID like "MCP-H1-0001"

    Returns:
        Datapoint dictionary or None if not found
    """
    base_path = Path(base_path)

    # Parse ID to get category (e.g., "H1" from "MCP-H1-0001")
    parts = datapoint_id.split("-")
    if len(parts) >= 2:
        category = parts[1]

        # Try direct path first
        direct_path = base_path / "datapoints" / category / f"{datapoint_id}.json"
        if direct_path.exists():
            with open(direct_path, "r", encoding="utf-8") as f:
                return json.load(f)

    # Fall back to searching all files
    for json_file in base_path.glob("datapoints/*/MCP-*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                dp = json.load(f)
                if dp.get("datapoint_id") == datapoint_id:
                    return dp
        except (json.JSONDecodeError, OSError):
            continue

    return None


def load_datapoints_by_category(
    base_path: Path | str,
    category: str
) -> list[dict]:
    """
    Load all datapoints for a specific attack category.

    Args:
        base_path: Path to the datasets/tool-calling directory
        category: Attack category like "H1", "S1", "U3"

    Returns:
        List of datapoint dictionaries for the category
    """
    base_path = Path(base_path)
    datapoints = []

    # Look in category directory
    category_dir = base_path / "datapoints" / category
    if category_dir.is_dir():
        for json_file in category_dir.glob("MCP-*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    dp = json.load(f)
                    datapoints.append(dp)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: Failed to load {json_file}: {e}")

    # Sort by datapoint_id
    datapoints.sort(key=lambda x: x.get("datapoint_id", ""))

    return datapoints


def validate_datapoint(datapoint: dict) -> list[str]:
    """
    Validate a datapoint has all required fields.

    Args:
        datapoint: Datapoint dictionary

    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []

    required_fields = [
        "datapoint_id",
        "system_instruction",
        "available_tools",
        "conversation",
        "metadata",
        "forbidden_actions",
        "evaluation_rubric"
    ]

    for field in required_fields:
        if field not in datapoint:
            errors.append(f"Missing required field: {field}")

    # Check metadata fields
    if "metadata" in datapoint:
        metadata = datapoint["metadata"]
        required_metadata = ["mcp_attack_type", "mcp_attack_layer", "severity"]
        for field in required_metadata:
            if field not in metadata:
                errors.append(f"Missing metadata field: {field}")

    # Check evaluation_rubric (new structure)
    if "evaluation_rubric" in datapoint:
        rubric = datapoint["evaluation_rubric"]

        # Check safety_rubric (now an array of criteria strings)
        if "safety_rubric" not in rubric:
            errors.append("Missing evaluation_rubric.safety_rubric")
        elif not isinstance(rubric["safety_rubric"], list):
            errors.append("evaluation_rubric.safety_rubric must be an array")
        elif len(rubric["safety_rubric"]) < 1:
            errors.append("evaluation_rubric.safety_rubric must have at least 1 item")


    return errors
