#!/usr/bin/env python3
"""
OTIO Sync Protocol — Unified Documentation Generator

Generates a single HTML reference document covering both layers of the sync
protocol:
  - OTIO SyncEvent schemas (AST-parsed from the Python source file)
  - Transport-layer ProtocolMessages (introspected from the registry)

All configuration lives in a single config.yml with three top-level sections:
  meta:               title, otio_input path, output path, optional introduction
  otio_events:        class-name keyed examples/categories for SyncEvents
  protocol_messages:  class-name keyed examples/categories for ProtocolMessages

Usage:
    python doc_generator.py --config docs/config.yml [--output docs.html]
"""

import ast
import argparse
import re
import json
import sys
import yaml
import importlib.util
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Parameter:
    """A field/parameter in a schema."""
    name: str
    type_hint: str
    description: str
    required: bool = True


@dataclass
class SchemaClass:
    """A parsed schema entry (OTIO SyncEvent or ProtocolMessage)."""
    name: str
    schema_label: str   # OTIO: "_serializable_label"; Protocol: command_schema
    base_class: str
    description: str
    parameters: List[Parameter]
    category: str
    source_type: str = "otio"   # "otio" | "protocol"
    event: str = ""             # protocol-only: event name


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path: str) -> Dict[str, Any]:
    """Load unified config.yml and return {meta, otio_events, protocol_messages}."""
    p = Path(path)
    with open(p) as f:
        raw = yaml.safe_load(f) or {}
    return {
        "meta": raw.get("meta", {}),
        "otio_events": raw.get("otio_events", {}),
        "protocol_messages": raw.get("protocol_messages", {}),
    }


def _extract_categories(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build {category: [class_name, ...]} from a class-keyed config dict."""
    categories: Dict[str, List[str]] = {}
    for class_name, entry in config.items():
        if isinstance(entry, dict) and "_category" in entry:
            cat = entry["_category"]
            categories.setdefault(cat, []).append(class_name)
    return categories


# ---------------------------------------------------------------------------
# OTIO SyncEvent parsing
# ---------------------------------------------------------------------------

class OTIOSchemaParser:
    """AST-parses a Python source file to extract @otio.core.register_type classes."""

    def __init__(self, file_path: str, otio_config: Dict[str, Any]):
        self.file_path = Path(file_path)
        self.tree = None
        self.schemas: List[SchemaClass] = []
        self.categories = _extract_categories(otio_config)

    def parse(self) -> List[SchemaClass]:
        with open(self.file_path) as f:
            content = f.read()
        self.tree = ast.parse(content)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                schema = self._parse_class(node, content)
                if schema:
                    self.schemas.append(schema)
        return self.schemas

    def _parse_class(self, node: ast.ClassDef, source: str) -> Optional[SchemaClass]:
        if not self._has_register_decorator(node):
            return None
        schema_label = self._extract_schema_label(node)
        if not schema_label:
            return None
        description = ast.get_docstring(node) or ""
        if "Attributes:" in description:
            description = description.split("Attributes:")[0].strip()
        base_class = "SyncEvent"
        if node.bases:
            if isinstance(node.bases[0], ast.Name):
                base_class = node.bases[0].id
            elif isinstance(node.bases[0], ast.Attribute):
                base_class = node.bases[0].attr
        parameters = self._extract_parameters(node, description)
        category = self._categorize(node.name)
        return SchemaClass(
            name=node.name,
            schema_label=schema_label,
            base_class=base_class,
            description=description,
            parameters=parameters,
            category=category,
            source_type="otio",
        )

    def _has_register_decorator(self, node: ast.ClassDef) -> bool:
        for dec in node.decorator_list:
            if isinstance(dec, ast.Attribute) and dec.attr == "register_type":
                return True
            if isinstance(dec, ast.Name) and dec.id == "register_type":
                return True
        return False

    def _extract_schema_label(self, node: ast.ClassDef) -> Optional[str]:
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "_serializable_label":
                        if isinstance(item.value, ast.Constant):
                            return item.value.value
        return None

    def _extract_parameters(self, node: ast.ClassDef, docstring: str) -> List[Parameter]:
        parameters = []
        doc_params = self._parse_docstring_attributes(docstring)
        init_method = None
        field_info: Dict[str, tuple] = {}
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                init_method = item
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        if isinstance(item.value, ast.Call) and self._is_serializable_field(item.value):
                            field_info[target.id] = (
                                self._extract_field_type(item.value),
                                self._extract_field_doc(item.value),
                            )
        if init_method:
            defaults_offset = len(init_method.args.args) - len(init_method.args.defaults)
            for idx, arg in enumerate(init_method.args.args):
                if arg.arg == "self":
                    continue
                name = arg.arg
                if name in field_info and field_info[name][0]:
                    type_hint = field_info[name][0]
                else:
                    type_hint = self._get_type_hint(arg)
                description = ""
                if name in field_info and field_info[name][1]:
                    description = field_info[name][1]
                elif name in doc_params:
                    description = doc_params[name]
                parameters.append(Parameter(
                    name=name,
                    type_hint=type_hint,
                    description=description,
                    required=idx < defaults_offset,
                ))
        return parameters

    def _is_serializable_field(self, call: ast.Call) -> bool:
        if isinstance(call.func, ast.Attribute):
            return call.func.attr == "serializable_field"
        if isinstance(call.func, ast.Name):
            return call.func.id == "serializable_field"
        return False

    def _extract_field_type(self, call: ast.Call) -> Optional[str]:
        for kw in call.keywords:
            if kw.arg == "required_type":
                return self._annotation_to_string(kw.value)
        return None

    def _extract_field_doc(self, call: ast.Call) -> Optional[str]:
        for kw in call.keywords:
            if kw.arg == "doc" and isinstance(kw.value, ast.Constant):
                return kw.value.value
        return None

    def _parse_docstring_attributes(self, docstring: str) -> Dict[str, str]:
        attributes: Dict[str, str] = {}
        if not docstring:
            return attributes
        lines = docstring.split("\n")
        in_attrs = False
        current_attr = None
        current_desc: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("Attributes:") or stripped.startswith("Attribute:"):
                in_attrs = True
                continue
            if in_attrs:
                m = re.match(r"^(\w+)\s*\(([^)]+)\):\s*(.+)$", stripped)
                if m:
                    if current_attr:
                        attributes[current_attr] = " ".join(current_desc)
                    current_attr = m.group(1)
                    current_desc = [m.group(3)]
                elif stripped and current_attr:
                    current_desc.append(stripped)
                elif not stripped and current_attr:
                    break
        if current_attr:
            attributes[current_attr] = " ".join(current_desc)
        return attributes

    def _get_type_hint(self, arg: ast.arg) -> str:
        if arg.annotation:
            return self._annotation_to_string(arg.annotation)
        return "str"

    def _annotation_to_string(self, annotation) -> str:
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Constant):
            return str(annotation.value)
        if isinstance(annotation, ast.Attribute):
            parts = []
            node = annotation
            while isinstance(node, ast.Attribute):
                parts.insert(0, node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.insert(0, node.id)
            full = ".".join(parts)
            return parts[-1] if "otio" in full else full
        if isinstance(annotation, ast.Subscript):
            return f"{self._annotation_to_string(annotation.value)}[{self._annotation_to_string(annotation.slice)}]"
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return f"{self._annotation_to_string(annotation.left)} | {self._annotation_to_string(annotation.right)}"
        if isinstance(annotation, ast.Tuple):
            return f"({', '.join(self._annotation_to_string(e) for e in annotation.elts)})"
        return "Any"

    def _categorize(self, class_name: str) -> str:
        for cat, names in self.categories.items():
            if class_name in names:
                return cat
        return "Unknown"


# ---------------------------------------------------------------------------
# Protocol message collection
# ---------------------------------------------------------------------------

def collect_protocol_messages(protocol_config: Dict[str, Any]) -> List[SchemaClass]:
    """Introspect registered ProtocolMessages and build SchemaClass instances."""
    try:
        _REPO_ROOT = Path(__file__).resolve().parent.parent
        if str(_REPO_ROOT / "python") not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT / "python"))
        from otio_sync_core import protocol_messages as pm
    except ImportError as exc:
        print(f"ERROR: Cannot import otio_sync_core.protocol_messages: {exc}", file=sys.stderr)
        print("Make sure the project's python/ directory is on PYTHONPATH.", file=sys.stderr)
        sys.exit(1)

    schemas: List[SchemaClass] = []
    for (schema, event), cls in pm.registered_messages().items():
        entry = protocol_config.get(cls.__name__, {}) or {}
        category = entry.get("_category", "Uncategorized")
        description = (cls.__doc__ or "").strip().split("\n\n")[0].strip()
        fields = cls.doc_fields()
        parameters = [
            Parameter(name=name, type_hint=ftype, description=doc, required=False)
            for name, ftype, doc in fields
        ]
        schemas.append(SchemaClass(
            name=cls.__name__,
            schema_label=schema,
            base_class="ProtocolMessage",
            description=description,
            parameters=parameters,
            category=category,
            source_type="protocol",
            event=event,
        ))
    schemas.sort(key=lambda s: (s.category, s.schema_label, s.event))
    return schemas


# ---------------------------------------------------------------------------
# Example generation
# ---------------------------------------------------------------------------

class ExampleGenerator:
    """Generates JSON and Python example snippets for both OTIO and protocol schemas."""

    def __init__(self, module_path: str, otio_config: Dict[str, Any], protocol_config: Dict[str, Any]):
        self.module_path = Path(module_path)
        self.module = None
        self._load_module()

        # Strip _category from both configs; keyed by class name → {example_name: params}
        self.examples: Dict[str, Any] = {}
        for cls_name, entry in otio_config.items():
            if isinstance(entry, dict):
                self.examples[cls_name] = {k: v for k, v in entry.items() if k != "_category"}

        self.protocol_examples: Dict[str, Any] = {}
        for cls_name, entry in protocol_config.items():
            if isinstance(entry, dict):
                self.protocol_examples[cls_name] = {k: v for k, v in entry.items() if k != "_category"}

    def _load_module(self):
        spec = importlib.util.spec_from_file_location("schema_module", self.module_path)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules["schema_module"] = self.module
        spec.loader.exec_module(self.module)

    # --- OTIO examples ---

    def generate_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate OTIO JSON example by instantiating the class."""
        try:
            cls = getattr(self.module, class_name, None)
            if cls is None:
                return None
            params = self._get_otio_params(class_name, example_name)
            params = self._resolve_otio_objects(params)
            instance = cls(**params)
            try:
                json_str = instance.to_json_string()
            except AttributeError:
                import opentimelineio as otio
                json_str = otio.core.serialize_json_to_string(instance)
            return self._format_json_with_highlighting(json_str)
        except Exception as e:
            print(f"Warning: Could not generate OTIO example for {class_name}: {e}")
            return None

    def generate_python_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate OTIO Python instantiation example."""
        try:
            cls = getattr(self.module, class_name, None)
            if cls is None:
                return None
            params = self._get_otio_params(class_name, example_name)
            code = (
                "# OTIO SyncEvent (serialized as OpenTimelineIO object)\n"
                "import opentimelineio as otio\n"
                "SyncEvent = otio.schema.schemadef.module_from_name('SyncEvent')\n\n"
                f"event = otio.schema.schemadef.{class_name}({self._format_param_str(params)})"
            )
            try:
                import black
                code = black.format_str(code, mode=black.Mode())
            except Exception:
                pass
            return self._format_python_with_highlighting(code)
        except Exception as e:
            print(f"Warning: Could not generate OTIO Python example for {class_name}: {e}")
            return None

    def _get_otio_params(self, class_name: str, example_name: str) -> dict:
        class_examples = self.examples.get(class_name, {})
        if example_name in class_examples:
            return class_examples[example_name]
        if "default" in class_examples:
            return class_examples["default"]
        if class_examples and not any(k in ["default", "example1", "example2"] for k in class_examples):
            return class_examples
        return {}

    # --- Protocol examples ---

    def generate_protocol_json_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate protocol message JSON example from config params."""
        params = self._get_protocol_params(class_name, example_name)
        if params is None:
            return None
        try:
            json_str = json.dumps(params, indent=2)
            return self._format_json_with_highlighting(json_str)
        except Exception as e:
            print(f"Warning: Could not generate protocol JSON example for {class_name}: {e}")
            return None

    def generate_protocol_python_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate protocol message Python instantiation example."""
        params = self._get_protocol_params(class_name, example_name)
        if params is None:
            return None
        try:
            param_str = ", ".join(f"{k}={repr(v)}" for k, v in params.items())
            code = (
                f"# Protocol Message (transport layer)\n"
                f"from otio_sync_core.protocol_messages import {class_name}\n\n"
                f"msg = {class_name}({param_str})"
            )
            try:
                import black
                code = black.format_str(code, mode=black.Mode())
            except Exception:
                pass
            return self._format_python_with_highlighting(code)
        except Exception as e:
            print(f"Warning: Could not generate protocol Python example for {class_name}: {e}")
            return None

    def _get_protocol_params(self, class_name: str, example_name: str) -> Optional[dict]:
        class_examples = self.protocol_examples.get(class_name, {})
        if not class_examples:
            return None
        if example_name in class_examples:
            return class_examples[example_name]
        if "default" in class_examples:
            return class_examples["default"]
        return None

    # --- Shared helpers ---

    def get_available_examples(self, class_name: str, source_type: str = "otio") -> List[str]:
        config = self.protocol_examples if source_type == "protocol" else self.examples
        class_examples = config.get(class_name, {})
        if isinstance(class_examples, dict) and class_examples:
            if any(k in ["default", "example1", "example2"] for k in class_examples):
                return list(class_examples.keys())
        return ["default"]

    def _resolve_otio_objects(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import opentimelineio as otio
        resolved = {}
        for key, value in params.items():
            if isinstance(value, dict):
                if "OTIO_SCHEMA" in value:
                    try:
                        resolved[key] = otio.adapters.read_from_string(
                            json.dumps(value), adapter_name="otio_json"
                        )
                    except Exception:
                        resolved[key] = self._resolve_custom_schema(value)
                else:
                    resolved[key] = self._resolve_otio_objects(value)
            elif isinstance(value, list):
                resolved[key] = [
                    self._resolve_otio_objects({"item": v})["item"] if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                resolved[key] = value
        return resolved

    def _resolve_custom_schema(self, spec: Dict[str, Any]) -> Any:
        schema_name = spec.get("OTIO_SCHEMA", "").split(".")[0]
        cls = getattr(self.module, schema_name, None)
        if cls:
            params = {k: v for k, v in spec.items() if k != "OTIO_SCHEMA"}
            return cls(**self._resolve_otio_objects(params))
        return spec

    def _format_json_with_highlighting(self, json_str: str) -> str:
        try:
            obj = json.loads(json_str)
            formatted = json.dumps(obj, indent=2)
        except Exception:
            formatted = json_str
        highlighted = formatted
        highlighted = re.sub(r'"([^"]+)"\s*:', r'<span class="json-key">"\1"</span>:', highlighted)
        highlighted = re.sub(r':\s*"([^"]*)"', r': <span class="json-string">"\1"</span>', highlighted)
        highlighted = re.sub(r':\s*(-?\d+\.?\d*)', r': <span class="json-number">\1</span>', highlighted)
        highlighted = re.sub(r':\s*(true|false)', r': <span class="json-boolean">\1</span>', highlighted)
        highlighted = re.sub(r':\s*(null)', r': <span class="json-null">\1</span>', highlighted)
        return highlighted

    def _format_python_with_highlighting(self, code: str) -> str:
        keywords = r"\b(def|class|return|if|else|elif|for|while|import|from|as|with|try|except|finally|pass|break|continue|in|is|not|and|or|lambda|yield|global|nonlocal|assert|del|raise|True|False|None)\b"
        code = re.sub(keywords, r'<span class="py-keyword">\1</span>', code)
        code = re.sub(r"(\'[^\']*\'|\"[^\"]*\")", r'<span class="py-string">\1</span>', code)
        code = re.sub(r"(?<![\w.])(-?\d+\.?\d*)", r'<span class="py-number">\1</span>', code)
        code = re.sub(r"(#.*)", r'<span class="py-comment">\1</span>', code)
        return code

    def _format_param_str(self, params: dict) -> str:
        """Format params dict as keyword arguments string (simple repr, no OTIO resolution)."""
        def fmt(val):
            if isinstance(val, dict):
                if "OTIO_SCHEMA" in val:
                    schema = val["OTIO_SCHEMA"]
                    if schema.startswith("RationalTime"):
                        return f"otio.opentime.RationalTime(value={val.get('value', 0)}, rate={val.get('rate', 24)})"
                    if schema.startswith("TimeRange"):
                        start = fmt(val.get("start_time", {}))
                        dur = fmt(val.get("duration", {}))
                        return f"otio.opentime.TimeRange(start_time={start}, duration={dur})"
                    custom = schema.split(".")[0]
                    inner = ", ".join(f"{k}={fmt(v)}" for k, v in val.items() if k != "OTIO_SCHEMA")
                    return f"otio.schema.schemadef.{custom}({inner})"
                return "{" + ", ".join(f"{repr(k)}: {fmt(v)}" for k, v in val.items()) + "}"
            if isinstance(val, list):
                return "[" + ", ".join(fmt(v) for v in val) + "]"
            return repr(val)
        return ", ".join(f"{k}={fmt(v)}" for k, v in params.items())


# ---------------------------------------------------------------------------
# HTML generator
# ---------------------------------------------------------------------------

class HTMLDocGenerator:
    """Generates a single HTML document covering both OTIO and protocol schemas."""

    def __init__(
        self,
        otio_schemas: List[SchemaClass],
        otio_category_order: List[str],
        protocol_schemas: List[SchemaClass],
        protocol_category_order: List[str],
        example_generator: ExampleGenerator,
        meta: Dict[str, Any],
    ):
        self.example_generator = example_generator
        self.meta = meta
        self.otio_categories = self._organize(otio_schemas, otio_category_order)
        self.protocol_categories = self._organize(protocol_schemas, protocol_category_order)

    def _organize(self, schemas: List[SchemaClass], order: List[str]) -> Dict[str, List[SchemaClass]]:
        result: Dict[str, List[SchemaClass]] = {cat: [] for cat in order}
        for s in schemas:
            if s.category in result:
                result[s.category].append(s)
        return result

    def generate(self, output_path: str):
        html = self._generate_html()
        with open(output_path, "w") as f:
            f.write(html)
        print(f"Documentation generated: {output_path}")

    def _generate_html(self) -> str:
        title = self.meta.get("title", "OTIO Sync Protocol Documentation")
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    {self._generate_css()}
</head>
<body>
    <div class="container">
        {self._generate_sidebar()}
        {self._generate_content()}
    </div>
    {self._generate_javascript()}
</body>
</html>"""

    def _generate_css(self) -> str:
        return """<style>
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }

        .container { display: flex; min-height: 100vh; }

        .sidebar {
            width: 280px;
            background: #2c3e50;
            color: #ecf0f1;
            position: fixed;
            height: 100vh;
            overflow-y: auto;
            padding: 20px;
        }

        .sidebar h1 { font-size: 1.4em; margin-bottom: 20px; color: #3498db; }

        .sidebar-section {
            font-size: 0.7em;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: #7f8c8d;
            margin-top: 20px;
            margin-bottom: 5px;
            padding: 10px 10px 0;
            border-top: 1px solid #34495e;
        }

        .sidebar h2 {
            font-size: 1em;
            margin-top: 12px;
            margin-bottom: 6px;
            color: #ecf0f1;
            text-transform: uppercase;
            font-weight: 600;
        }

        .sidebar ul { list-style: none; }
        .sidebar li { margin: 4px 0; }

        .sidebar a {
            color: #bdc3c7;
            text-decoration: none;
            display: block;
            padding: 4px 10px;
            border-radius: 4px;
            transition: all 0.2s;
        }

        .sidebar a:hover { background: #34495e; color: #3498db; }

        .content { margin-left: 280px; padding: 40px; flex: 1; max-width: 1200px; }

        .section {
            background: white;
            padding: 30px;
            margin-bottom: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }

        h2 { color: #2c3e50; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 3px solid #3498db; }
        h3 { color: #34495e; margin-top: 25px; margin-bottom: 10px; }

        .schema-label {
            display: inline-block;
            background: #3498db;
            color: white;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.85em;
            font-weight: bold;
            margin-bottom: 10px;
        }

        .event-label {
            display: inline-block;
            background: #27ae60;
            color: white;
            padding: 3px 8px;
            border-radius: 3px;
            font-size: 0.85em;
            font-weight: bold;
            margin-bottom: 10px;
            margin-left: 6px;
        }

        .description {
            background: #ecf0f1;
            padding: 15px;
            border-left: 4px solid #3498db;
            margin: 15px 0;
            border-radius: 4px;
            white-space: pre-wrap;
        }

        .params-table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        .params-table th { background: #34495e; color: white; padding: 12px; text-align: left; font-weight: 600; }
        .params-table td { padding: 12px; border-bottom: 1px solid #ecf0f1; }
        .params-table tr:hover { background: #f8f9fa; }

        .type-badge {
            background: #e74c3c;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.85em;
            font-family: monospace;
        }

        .required-badge {
            background: #f39c12;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.75em;
            margin-left: 5px;
        }

        .code-block {
            background: #282c34;
            color: #abb2bf;
            padding: 20px;
            border-radius: 6px;
            overflow-x: auto;
            margin: 15px 0;
            position: relative;
        }

        .code-block pre {
            margin: 0;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            line-height: 1.5;
        }

        .copy-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            background: #3498db;
            color: white;
            border: none;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.85em;
            transition: background 0.2s;
        }

        .copy-btn:hover { background: #2980b9; }
        .copy-btn.copied { background: #27ae60; }

        .json-key { color: #e06c75; }
        .json-string { color: #98c379; }
        .json-number { color: #d19a66; }
        .json-boolean { color: #56b6c2; }
        .json-null { color: #c678dd; }
        .py-keyword { color: #c678dd; }
        .py-string { color: #98c379; }
        .py-number { color: #d19a66; }
        .py-comment { color: #5c6370; font-style: italic; }

        code {
            // background: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
        }

        .note {
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px;
            margin: 15px 0;
            border-radius: 4px;
        }

        .note strong { color: #856404; }

        .example-tabs { display: flex; gap: 5px; margin-bottom: -1px; flex-wrap: wrap; }

        .example-tab {
            padding: 8px 16px;
            background: #ecf0f1;
            border: none;
            border-radius: 6px 6px 0 0;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.2s;
            font-size: 0.9em;
        }

        .example-tab:hover { background: #bdc3c7; }
        .example-tab.active { background: #282c34; color: white; }
        .example-content { display: none; }
        .example-content.active { display: block; }

        /* Introduction section Markdown styles */
        .intro-content h1, .intro-content h2, .intro-content h3 { margin: 1em 0 0.5em; }
        .intro-content p { margin: 0.75em 0; }
        .intro-content ul, .intro-content ol { margin: 0.5em 0 0.5em 1.5em; }
        .intro-content code { 
            // background: #f4f4f4; 
            padding: 2px 5px; 
            border-radius: 3px; 
        }
        .intro-content pre { background: #282c34; color: #abb2bf; padding: 15px; border-radius: 6px; overflow-x: auto; }
        .intro-content table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.95em; }
        .intro-content th { background: #34495e; color: white; padding: 10px 12px; text-align: left; font-weight: 600; }
        .intro-content td { padding: 10px 12px; border-bottom: 1px solid #ecf0f1; vertical-align: top; }
        .intro-content tr:hover td { background: #f8f9fa; }
        .intro-content blockquote { border-left: 4px solid #3498db; margin: 1em 0; padding: 10px 15px; background: #ecf0f1; border-radius: 0 4px 4px 0; }
        </style>"""

    def _generate_sidebar(self) -> str:
        title = self.meta.get("title", "OTIO Sync Protocol")
        html = f'<nav class="sidebar"><h1>{title}</h1>'

        html += '<div class="sidebar-section">OTIO SyncEvents</div>'
        for cat, schemas in self.otio_categories.items():
            if schemas:
                html += f'<h2>{cat} Events</h2><ul>'
                for s in schemas:
                    html += f'<li><a href="#{self._anchor(s.name)}">{s.name}</a></li>'
                html += "</ul>"

        html += '<div class="sidebar-section">Protocol Messages</div>'
        for cat, schemas in self.protocol_categories.items():
            if schemas:
                html += f'<h2>{cat}</h2><ul>'
                for s in schemas:
                    html += f'<li><a href="#{self._anchor(s.name)}">{s.name}</a></li>'
                html += "</ul>"

        html += "</nav>"
        return html

    def _generate_content(self) -> str:
        content = '<main class="content">'
        content += self._render_introduction()

        for cat, schemas in self.otio_categories.items():
            for s in schemas:
                content += self._generate_schema_section(s)

        for cat, schemas in self.protocol_categories.items():
            for s in schemas:
                content += self._generate_schema_section(s)

        content += "</main>"
        return content

    def _render_introduction(self) -> str:
        intro_path = self.meta.get("introduction")
        if intro_path:
            p = Path(intro_path)
            if not p.is_absolute():
                p = Path(self.meta.get("_config_dir", ".")) / p
            if p.exists():
                try:
                    import mistune
                except ImportError:
                    print("ERROR: mistune is not installed. Run: pip install mistune==3.1.1", file=sys.stderr)
                    sys.exit(1)
                try:
                    md = mistune.create_markdown(plugins=["table", "strikethrough"])
                    rendered = md(p.read_text())
                    return f'<div class="section"><div class="intro-content">{rendered}</div></div>'
                except Exception as e:
                    print(f"Warning: Could not render introduction Markdown: {e}", file=sys.stderr)

        return """<div class="section">
            <h2>Overview</h2>
            <p>This documentation describes the OTIO-based synchronized review messaging protocol.
            The OTIO SyncEvents are serialized as OpenTimelineIO objects; the Protocol Messages
            are the transport-layer envelopes exchanged over the sync network.</p>
            <div class="note">
                <strong>Note:</strong> Generated automatically from Python schema definitions
                and the protocol message registry.
            </div>
        </div>"""

    def _generate_schema_section(self, schema: SchemaClass) -> str:
        anchor = self._anchor(schema.name)
        badges = f'<span class="schema-label">{schema.schema_label}</span>'
        if schema.source_type == "protocol" and schema.event:
            badges += f'<span class="event-label">event: {schema.event}</span>'

        section = f"""
        <div id="{anchor}" class="section">
            <h2>{schema.name}</h2>
            {badges}
            <div class="description">{schema.description}</div>"""

        if schema.parameters:
            section += """
            <h3>Parameters</h3>
            <table class="params-table">
                <thead><tr><th>Parameter</th><th>Type</th><th>Description</th></tr></thead>
                <tbody>"""
            for param in schema.parameters:
                opt = '' if param.required else '<span class="required-badge">Optional</span>'
                section += f"""
                    <tr>
                        <td><code>{param.name}</code>{opt}</td>
                        <td><span class="type-badge">{param.type_hint}</span></td>
                        <td>{param.description}</td>
                    </tr>"""
            section += "</tbody></table>"

        section += self._generate_examples_section(schema)
        section += "</div>"
        return section

    def _generate_examples_section(self, schema: SchemaClass) -> str:
        example_names = self.example_generator.get_available_examples(schema.name, schema.source_type)
        if not example_names:
            return ""

        plural = "s" if len(example_names) > 1 else ""
        section = f"<h3>Example{plural}</h3>"
        tabs_id = self._anchor(schema.name)

        # Tab buttons
        section += f'<div class="example-tabs" id="tabs-{tabs_id}">'
        for i, ex_name in enumerate(example_names):
            active = "active" if i == 0 else ""
            label = ex_name.replace("_", " ").title()
            section += f'<button class="example-tab {active}" onclick="switchExampleTab(event, \'{tabs_id}\', \'{ex_name}\')">{label}</button>'
            section += f'<button class="example-tab {active}" onclick="switchExampleTab(event, \'{tabs_id}\', \'{ex_name}_python\')">{label} (Python)</button>'
        section += "</div>"

        # Tab content
        for i, ex_name in enumerate(example_names):
            active = "active" if i == 0 else ""
            if schema.source_type == "protocol":
                json_content = self.example_generator.generate_protocol_json_example(schema.name, ex_name)
                py_content = self.example_generator.generate_protocol_python_example(schema.name, ex_name)
            else:
                json_content = self.example_generator.generate_example(schema.name, ex_name)
                py_content = self.example_generator.generate_python_example(schema.name, ex_name)

            if json_content:
                section += f"""
                <div id="{tabs_id}-{ex_name}" class="example-content {active}">
                    <div class="code-block">
                        <button class="copy-btn" onclick="copyCode(this)">Copy</button>
                        <pre>{json_content}</pre>
                    </div>
                </div>"""
            if py_content:
                section += f"""
                <div id="{tabs_id}-{ex_name}_python" class="example-content">
                    <div class="code-block">
                        <button class="copy-btn" onclick="copyCode(this)">Copy</button>
                        <pre>{py_content}</pre>
                    </div>
                </div>"""

        return section

    def _anchor(self, name: str) -> str:
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1-\2", name)
        return re.sub("([a-z0-9])([A-Z])", r"\1-\2", s1).lower()

    def _generate_javascript(self) -> str:
        return """<script>
        function copyCode(button) {
            const pre = button.nextElementSibling;
            navigator.clipboard.writeText(pre.textContent).then(() => {
                button.textContent = 'Copied!';
                button.classList.add('copied');
                setTimeout(() => { button.textContent = 'Copy'; button.classList.remove('copied'); }, 2000);
            });
        }

        function switchExampleTab(event, schemaId, exampleName) {
            const tabContainer = document.getElementById('tabs-' + schemaId);
            tabContainer.querySelectorAll('.example-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('[id^="' + schemaId + '-"]').forEach(c => c.classList.remove('active'));
            event.currentTarget.classList.add('active');
            const content = document.getElementById(schemaId + '-' + exampleName);
            if (content) content.classList.add('active');
        }

        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function(e) {
                e.preventDefault();
                const target = document.querySelector(this.getAttribute('href'));
                if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            });
        });
        </script>"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate unified HTML documentation for the OTIO Sync Protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python doc_generator.py --config docs/config.yml
  python doc_generator.py --config docs/config.yml --output my_docs.html
        """,
    )
    parser.add_argument("--config", "-c", default="config.yml",
                        help="Path to the unified config.yml (default: config.yml)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output HTML path (overrides meta.output in config)")
    args = parser.parse_args()

    print(f"Loading config from {args.config}...")
    config = load_config(args.config)
    meta = config["meta"]
    otio_cfg = config["otio_events"]
    proto_cfg = config["protocol_messages"]

    # Store config dir so _render_introduction can resolve relative paths
    meta["_config_dir"] = str(Path(args.config).resolve().parent)

    if args.output:
        # CLI --output is relative to CWD
        output_path = args.output
    else:
        # meta.output is relative to the config file
        raw = meta.get("output", "otio_sync_docs.html")
        output_path = raw if Path(raw).is_absolute() else str(Path(args.config).parent / raw)

    otio_input = meta.get("otio_input")
    if not otio_input:
        parser.error("meta.otio_input is required in the config file")
    if not Path(otio_input).is_absolute():
        otio_input = str(Path(args.config).parent / otio_input)
    if not Path(otio_input).exists():
        parser.error(f"otio_input file not found: {otio_input}")

    # Parse OTIO schemas
    print(f"Parsing OTIO schemas from {otio_input}...")
    otio_parser = OTIOSchemaParser(otio_input, otio_cfg)
    otio_schemas = otio_parser.parse()
    otio_categories = list(otio_parser.categories.keys())
    print(f"  Found {len(otio_schemas)} OTIO schemas in {len(otio_categories)} categories")

    # Collect protocol messages
    print("Collecting protocol messages...")
    proto_schemas = collect_protocol_messages(proto_cfg)
    proto_category_order = list(dict.fromkeys(s.category for s in proto_schemas))
    print(f"  Found {len(proto_schemas)} protocol messages in {len(proto_category_order)} categories")

    # Build example generator
    print("Loading example generator...")
    example_gen = ExampleGenerator(otio_input, otio_cfg, proto_cfg)

    # Generate HTML
    print("Generating HTML documentation...")
    generator = HTMLDocGenerator(
        otio_schemas=otio_schemas,
        otio_category_order=otio_categories,
        protocol_schemas=proto_schemas,
        protocol_category_order=proto_category_order,
        example_generator=example_gen,
        meta=meta,
    )
    generator.generate(output_path)
    print("Done!")


if __name__ == "__main__":
    main()
