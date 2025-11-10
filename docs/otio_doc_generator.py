#!/usr/bin/env python3
"""
OTIO Sync Protocol Documentation Generator

This script generates HTML documentation from OTIO SyncEvent schema definitions.
It parses the Python class definitions to extract schema information, parameters,
and docstrings, then generates an interactive HTML page.

Usage:
    python generate_docs.py --input SyncEvent.py --config examples.yaml --output docs.html
"""

import ast
import argparse
import re
import json
import yaml
import black
import importlib.util
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass


@dataclass
class Parameter:
    """Represents a parameter in a schema."""
    name: str
    type_hint: str
    description: str
    required: bool = True


@dataclass
class SchemaClass:
    """Represents a parsed schema class."""
    name: str
    schema_label: str
    base_class: str
    description: str
    parameters: List[Parameter]
    category: str  # Session, Playback, or Annotation


class OTIOSchemaParser:
    """Parses Python files containing OTIO schema definitions."""
    
    def __init__(self, file_path: str, config_path: Optional[str] = None):
        self.file_path = Path(file_path)
        self.tree = None
        self.schemas: List[SchemaClass] = []
        self.categories = self._load_config(config_path)

    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load examples configuration from YAML or JSON, so we can get the categories."""
        if config_path is None:
            return {}
        
        config_path = Path(config_path)
        with open(config_path, 'r') as f:
            if config_path.suffix in ['.yaml', '.yml']:
                conf = yaml.safe_load(f)
            else:
                conf = json.load(f)
        categories = {}
        for class_name, examples in conf.items():
            if isinstance(examples, dict):
                if "_category" in examples:
                    category = examples["_category"]
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(class_name)

        return categories

    def parse(self) -> List[SchemaClass]:
        """Parse the Python file and extract schema information."""
        with open(self.file_path, 'r') as f:
            content = f.read()
            self.tree = ast.parse(content)
        
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                schema = self._parse_class(node, content)
                if schema:
                    self.schemas.append(schema)
        
        return self.schemas
    
    def _parse_class(self, node: ast.ClassDef, source: str) -> Optional[SchemaClass]:
        """Parse a class definition to extract schema information."""
        # Skip if not decorated with @otio.core.register_type
        if not self._has_register_decorator(node):
            return None
        
        # Extract schema label
        schema_label = self._extract_schema_label(node)
        if not schema_label:
            return None
        
        # Extract docstring
        description = ast.get_docstring(node) or ""
        if "Attributes:" in description:
            description = description.split("Attributes:")[0].strip()
        
        # Determine base class
        base_class = "SyncEvent"
        if node.bases:
            if isinstance(node.bases[0], ast.Name):
                base_class = node.bases[0].id
            elif isinstance(node.bases[0], ast.Attribute):
                base_class = node.bases[0].attr
        
        # Extract parameters from __init__ and serializable_field definitions
        parameters = self._extract_parameters(node, description)
        
        # Categorize the schema
        category = self._categorize_schema(node.name)
        
        return SchemaClass(
            name=node.name,
            schema_label=schema_label,
            base_class=base_class,
            description=description,
            parameters=parameters,
            category=category
        )
    
    def _has_register_decorator(self, node: ast.ClassDef) -> bool:
        """Check if class has @otio.core.register_type decorator."""
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Attribute):
                if decorator.attr == "register_type":
                    return True
            elif isinstance(decorator, ast.Name):
                if decorator.id == "register_type":
                    return True
        return False
    
    def _extract_schema_label(self, node: ast.ClassDef) -> Optional[str]:
        """Extract _serializable_label from class."""
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name) and target.id == "_serializable_label":
                        if isinstance(item.value, ast.Constant):
                            return item.value.value
        return None
    
    def _extract_parameters(self, node: ast.ClassDef, docstring: str) -> List[Parameter]:
        """Extract parameters from __init__ and serializable_field definitions."""
        parameters = []
        
        # Extract from docstring Attributes section
        doc_params = self._parse_docstring_attributes(docstring)
        
        # Find __init__ method and collect field information
        init_method = None
        field_info = {}  # Maps field_name -> (type, doc)
        
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                init_method = item
            # Extract serializable_field information
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        field_name = target.id
                        if isinstance(item.value, ast.Call):
                            # Check if this is a serializable_field call
                            if self._is_serializable_field(item.value):
                                field_type = self._extract_field_type(item.value)
                                field_doc = self._extract_field_doc(item.value)
                                field_info[field_name] = (field_type, field_doc)
        
        if init_method:
            for arg in init_method.args.args:
                if arg.arg == "self":
                    continue
                
                param_name = arg.arg
                
                # Prioritize type from serializable_field, fallback to __init__ annotation
                if param_name in field_info and field_info[param_name][0]:
                    type_hint = field_info[param_name][0]
                else:
                    type_hint = self._get_type_hint(arg)
                
                # Get description from multiple sources, prioritizing in order
                description = ""
                if param_name in field_info and field_info[param_name][1]:
                    description = field_info[param_name][1]
                elif param_name in doc_params:
                    description = doc_params[param_name]
                
                # Check if parameter has default value
                defaults_offset = len(init_method.args.args) - len(init_method.args.defaults)
                arg_index = init_method.args.args.index(arg)
                has_default = arg_index >= defaults_offset
                
                parameters.append(Parameter(
                    name=param_name,
                    type_hint=type_hint,
                    description=description,
                    required=not has_default
                ))
        
        return parameters
    
    def _is_serializable_field(self, call_node: ast.Call) -> bool:
        """Check if a call is to serializable_field."""
        if isinstance(call_node.func, ast.Attribute):
            return call_node.func.attr == "serializable_field"
        elif isinstance(call_node.func, ast.Name):
            return call_node.func.id == "serializable_field"
        return False
    
    def _extract_field_type(self, call_node: ast.Call) -> Optional[str]:
        """Extract required_type from serializable_field call."""
        for keyword in call_node.keywords:
            if keyword.arg == "required_type":
                return self._annotation_to_string(keyword.value)
        return None
    
    def _parse_docstring_attributes(self, docstring: str) -> Dict[str, str]:
        """Parse Attributes section from docstring."""
        attributes = {}
        if not docstring:
            return attributes
        
        # Look for Attributes: section
        lines = docstring.split('\n')
        in_attributes = False
        current_attr = None
        current_desc = []
        
        for line in lines:
            stripped = line.strip()
            
            if stripped.startswith("Attributes:") or stripped.startswith("Attribute:"):
                in_attributes = True
                continue
            
            if in_attributes:
                # Check if this is a new attribute (starts with word and has :)
                match = re.match(r'^(\w+)\s*\(([^)]+)\):\s*(.+)$', stripped)
                if match:
                    # Save previous attribute
                    if current_attr:
                        attributes[current_attr] = ' '.join(current_desc)
                    
                    current_attr = match.group(1)
                    current_desc = [match.group(3)]
                elif stripped and current_attr:
                    # Continuation of description
                    current_desc.append(stripped)
                elif not stripped and current_attr:
                    # End of attributes section
                    break
        
        # Save last attribute
        if current_attr:
            attributes[current_attr] = ' '.join(current_desc)
        
        return attributes
    
    def _extract_field_doc(self, call_node: ast.Call) -> Optional[str]:
        """Extract doc string from serializable_field call."""
        for keyword in call_node.keywords:
            if keyword.arg == "doc":
                if isinstance(keyword.value, ast.Constant):
                    return keyword.value.value
        return None
    
    def _get_type_hint(self, arg: ast.arg) -> str:
        """Extract type hint from argument."""
        if arg.annotation:
            return self._annotation_to_string(arg.annotation)
        return "str"
    
    def _annotation_to_string(self, annotation) -> str:
        """Convert AST annotation to string."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Attribute):
            # Handle things like otio.opentime.RationalTime
            parts = []
            node = annotation
            while isinstance(node, ast.Attribute):
                parts.insert(0, node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.insert(0, node.id)
            # Simplify OTIO types for display
            full_name = '.'.join(parts)
            if 'otio' in full_name:
                # Extract just the class name for cleaner display
                return parts[-1]
            return full_name
        elif isinstance(annotation, ast.Subscript):
            base = self._annotation_to_string(annotation.value)
            slice_val = self._annotation_to_string(annotation.slice)
            return f"{base}[{slice_val}]"
        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            left = self._annotation_to_string(annotation.left)
            right = self._annotation_to_string(annotation.right)
            return f"{left} | {right}"
        elif isinstance(annotation, ast.Tuple):
            elements = [self._annotation_to_string(el) for el in annotation.elts]
            return f"({', '.join(elements)})"
        return "Any"
    
    def _categorize_schema(self, class_name: str) -> str:
        """Categorize schema based on class name."""

        for category, classes in self.categories.items():
            if class_name in classes:
                print("Class Name:", class_name, "Category:", category)
                return category
        print("Class Name:", class_name, "Category: Unknown")
        return "Unknown"
        playback_keywords = ["play", "frame", "playback", "sync", "media"]
        annotation_keywords = ["paint", "annotation", "text", "draw"]
        
        name_lower = class_name.lower()
        
        for keyword in playback_keywords:
            if keyword in name_lower:
                return "Playback"
        
        for keyword in annotation_keywords:
            if keyword in name_lower:
                return "Annotation"
        
        return "Session"


class ExampleGenerator:
    """Generates example instances from config and actual OTIO classes."""
    
    def __init__(self, module_path: str, config_path: Optional[str] = None):
        self.module_path = Path(module_path)
        self.config_path = Path(config_path) if config_path else None
        self.module = None
        self.examples = {}
        
        # Load the module
        self._load_module()
        
        # Load examples config if provided
        if self.config_path and self.config_path.exists():
            self._load_config()
    
    def _load_module(self):
        """Dynamically load the Python module containing schemas."""
        spec = importlib.util.spec_from_file_location("schema_module", self.module_path)
        self.module = importlib.util.module_from_spec(spec)
        sys.modules["schema_module"] = self.module
        spec.loader.exec_module(self.module)
    
    def _load_config(self):
        """Load examples configuration from YAML or JSON."""
        with open(self.config_path, 'r') as f:
            if self.config_path.suffix in ['.yaml', '.yml']:
                self.examples = yaml.safe_load(f)
            else:
                self.examples = json.load(f)
        # Remove any _category entries
        for examples in self.examples.values():
            if "_category" in examples:
                del examples["_category"]

    def generate_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate a JSON example by instantiating the class with config parameters."""
        try:
            # Get the class from the module
            cls = getattr(self.module, class_name, None)
            if cls is None:
                return None
            
            # Get example parameters from config
            if class_name in self.examples:
                class_examples = self.examples[class_name]
                if example_name in class_examples:
                    params = class_examples[example_name]
                elif "default" in class_examples:
                    params = class_examples["default"]
                elif isinstance(class_examples, dict) and not any(k in ["default", "example1", "example2"] for k in class_examples.keys()):
                    # Single example provided directly
                    params = class_examples
                else:
                    params = {}
            else:
                params = {}
            
            # Handle nested OTIO objects in params
            params = self._resolve_otio_objects(params)
            
            # Create instance
            instance = cls(**params)
            
            # Serialize to JSON using OTIO's serialization
            try:
                json_str = instance.to_json_string()
                return self._format_json_with_highlighting(json_str)
            except AttributeError:
                # Fallback if to_json_string not available
                import opentimelineio as otio
                json_dict = otio.core.serialize_json_to_string(instance)
                return self._format_json_with_highlighting(json_dict)
                
        except Exception as e:
            print(f"Warning: Could not generate example for {class_name}: {e}")
            return None
    
    def _resolve_otio_objects(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively resolve OTIO object specifications in parameters using OTIO's deserialization utilities."""
        import opentimelineio as otio
        resolved = {}
        for key, value in params.items():
            if isinstance(value, dict):
                if "OTIO_SCHEMA" in value:
                    # Use OTIO's deserialization for schema objects
                    try:
                        json_str = json.dumps(value)
                        resolved[key] = otio.adapters.read_from_string(json_str, adapter_name="otio_json")
                    except Exception as e:
                        print(f"Warning: Could not deserialize OTIO object for {key}: {e}")
                        # Fallback to manual resolution if deserialization fails
                        resolved[key] = self._resolve_custom_schema(value)
                else:
                    # Recursively resolve nested dicts
                    resolved[key] = self._resolve_otio_objects(value)
            elif isinstance(value, list):
                resolved[key] = [self._resolve_otio_objects({"item": v})["item"] if isinstance(v, dict) else v for v in value]
            else:
                resolved[key] = value
        return resolved
    
    def _resolve_custom_schema(self, spec: Dict[str, Any]) -> Any:
        """Attempt to resolve a custom OTIO schema from the loaded module."""
        schema_name = spec.get("OTIO_SCHEMA", "").split(".")[0]
        
        # Try to find the class in our module
        cls = getattr(self.module, schema_name, None)
        if cls:
            # Remove OTIO_SCHEMA from params
            params = {k: v for k, v in spec.items() if k != "OTIO_SCHEMA"}
            params = self._resolve_otio_objects(params)
            return cls(**params)
        
        return spec
    
    def _format_json_with_highlighting(self, json_str: str) -> str:
        """Add syntax highlighting to JSON string."""
        # Parse and pretty-print
        try:
            obj = json.loads(json_str)
            formatted = json.dumps(obj, indent=2)
        except:
            formatted = json_str
        
        # Add HTML highlighting
        highlighted = formatted
        
        # Highlight keys
        highlighted = re.sub(r'"([^"]+)"\s*:', r'<span class="json-key">"\1"</span>:', highlighted)
        
        # Highlight string values
        highlighted = re.sub(r':\s*"([^"]*)"', r': <span class="json-string">"\1"</span>', highlighted)
        
        # Highlight numbers
        highlighted = re.sub(r':\s*(-?\d+\.?\d*)', r': <span class="json-number">\1</span>', highlighted)
        
        # Highlight booleans
        highlighted = re.sub(r':\s*(true|false)', r': <span class="json-boolean">\1</span>', highlighted)
        
        # Highlight null
        highlighted = re.sub(r':\s*(null)', r': <span class="json-null">\1</span>', highlighted)
        
        return highlighted
    
    def get_available_examples(self, class_name: str) -> List[str]:
        """Get list of available example names for a class."""
        if class_name in self.examples:
            class_examples = self.examples[class_name]
            if isinstance(class_examples, dict):
                # Check if it's a multi-example config
                if any(k in ["default", "example1", "example2"] for k in class_examples.keys()):
                    return list(class_examples.keys())
        return ["default"]

    def _format_python_with_highlighting(self, code: str) -> str:
        """Add basic syntax highlighting to Python code using HTML span tags."""
        # Highlight keywords
        keywords = r"\b(def|class|return|if|else|elif|for|while|import|from|as|with|try|except|finally|pass|break|continue|in|is|not|and|or|lambda|yield|global|nonlocal|assert|del|raise|True|False|None)\b"
        code = re.sub(keywords, r'<span class="py-keyword">\1</span>', code)
        # Highlight strings
        code = re.sub(r'(\'[^\']*\'|\"[^\"]*\")', r'<span class="py-string">\1</span>', code)
        # Highlight numbers
        code = re.sub(r'(?<![\w.])(-?\d+\.?\d*)', r'<span class="py-number">\1</span>', code)
        # Highlight comments
        code = re.sub(r'(#.*)', r'<span class="py-comment">\1</span>', code)
        return code


    def generate_python_example(self, class_name: str, example_name: str = "default") -> Optional[str]:
        """Generate a pure Python example by instantiating the class with config parameters as code."""
        try:
            cls = getattr(self.module, class_name, None)
            if cls is None:
                return None
            if class_name in self.examples:
                class_examples = self.examples[class_name]
                if example_name in class_examples:
                    params = class_examples[example_name]
                elif "default" in class_examples:
                    params = class_examples["default"]
                elif isinstance(class_examples, dict) and not any(k in ["default", "example1", "example2"] for k in class_examples.keys()):
                    params = class_examples
                else:
                    params = {}
            else:
                params = {}
            code = self._format_python_instantiation(class_name, params)
            # Pretty-print using black if available
            try:
                import black
                code = black.format_str(code, mode=black.Mode())
                return code
            except Exception as e:
                print(e)
                pass  # Fallback to unformatted code if black is not available
            return None
        except Exception as e:
            print(f"Warning: Could not generate python example for {class_name}: {e}")
            return None

    def _format_python_instantiation(self, class_name: str, params: Dict[str, Any]) -> str:
        """Format the instantiation of a class as Python code, recursively handling nested OTIO objects."""
        def format_value(val):
            if isinstance(val, dict):
                if "OTIO_SCHEMA" in val:
                    schema_name = val["OTIO_SCHEMA"]
                    # Handle common OTIO types
                    if schema_name.startswith("RationalTime"):
                        v = val.get("value", 0)
                        r = val.get("rate", 24)
                        return f"otio.opentime.RationalTime(value={v}, rate={r})"
                    elif schema_name.startswith("TimeRange"):
                        start = format_value(val.get("start_time", {}))
                        duration = format_value(val.get("duration", {}))
                        return f"otio.opentime.TimeRange(start_time={start}, duration={duration})"
                    elif schema_name.startswith("Box2d"):
                        minv = val.get("min", {"x": 0, "y": 0})
                        maxv = val.get("max", {"x": 1, "y": 1})
                        min_str = f"otio.schema.V2d({minv['x']}, {minv['y']})"
                        max_str = f"otio.schema.V2d({maxv['x']}, {maxv['y']})"
                        return f"otio.schema.Box2d(min={min_str}, max={max_str})"
                    else:
                        # Custom schema
                        custom_class = schema_name.split(".")[0]
                        custom_params = {k: v for k, v in val.items() if k != "OTIO_SCHEMA"}
                        param_str = ', '.join(f"{k}={format_value(v)}" for k, v in custom_params.items())
                        return f"otio.schema.schemadef.{custom_class}({param_str})"
                else:
                    # Regular dict
                    return '{' + ', '.join(f"{repr(k)}: {format_value(v)}" for k, v in val.items()) + '}'
            elif isinstance(val, list):
                return '[' + ', '.join(format_value(v) for v in val) + ']'
            elif isinstance(val, str):
                return repr(val)
            else:
                return repr(val)
        param_str = ', '.join(f"{k}={format_value(v)}" for k, v in params.items())
        # Always use otio.schema.schemadef for class instantiation
        return (
            "import opentimelineio as otio\n"
            "SyncEvent = otio.schema.schemadef.module_from_name('SyncEvent')\n\n"
            f"event = otio.schema.schemadef.{class_name}({param_str})"
        )


class HTMLDocGenerator:
    """Generates HTML documentation from parsed schemas."""
    
    def __init__(self, schemas: List[SchemaClass], category_order: List[str], example_generator: ExampleGenerator):
        self.schemas = schemas
        self.example_generator = example_generator
        self.category_order = category_order
        self.categories = self._organize_by_category()
    
    def _organize_by_category(self) -> Dict[str, List[SchemaClass]]:
        """Organize schemas by category."""
        categories = {}
        for category in self.category_order:
            categories[category] = []
        for schema in self.schemas:
            if schema.category in categories:
                categories[schema.category].append(schema)
        return categories
    
    def generate(self, output_path: str):
        """Generate HTML documentation file."""
        html = self._generate_html()
        with open(output_path, 'w') as f:
            f.write(html)
        print(f"Documentation generated: {output_path}")
    
    def _generate_html(self) -> str:
        """Generate complete HTML document."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OTIO Sync Protocol Documentation</title>
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
        """Generate CSS styles."""
        return """<style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }
        
        .container {
            display: flex;
            min-height: 100vh;
        }
        
        .sidebar {
            width: 280px;
            background: #2c3e50;
            color: #ecf0f1;
            position: fixed;
            height: 100vh;
            overflow-y: auto;
            padding: 20px;
        }
        
        .sidebar h1 {
            font-size: 1.4em;
            margin-bottom: 20px;
            color: #3498db;
        }
        
        .sidebar h2 {
            font-size: 1.1em;
            margin-top: 20px;
            margin-bottom: 10px;
            color: #ecf0f1;
            text-transform: uppercase;
            font-weight: 600;
        }
        
        .sidebar ul {
            list-style: none;
        }
        
        .sidebar li {
            margin: 5px 0;
        }
        
        .sidebar a {
            color: #bdc3c7;
            text-decoration: none;
            display: block;
            padding: 5px 10px;
            border-radius: 4px;
            transition: all 0.2s;
        }
        
        .sidebar a:hover {
            background: #34495e;
            color: #3498db;
        }
        
        .content {
            margin-left: 280px;
            padding: 40px;
            flex: 1;
            max-width: 1200px;
        }
        
        .section {
            background: white;
            padding: 30px;
            margin-bottom: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        
        h2 {
            color: #2c3e50;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 3px solid #3498db;
        }
        
        h3 {
            color: #34495e;
            margin-top: 25px;
            margin-bottom: 10px;
        }
        
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
        
        .description {
            background: #ecf0f1;
            padding: 15px;
            border-left: 4px solid #3498db;
            margin: 15px 0;
            border-radius: 4px;
            white-space: pre-wrap;
        }
        
        .params-table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }
        
        .params-table th {
            background: #34495e;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }
        
        .params-table td {
            padding: 12px;
            border-bottom: 1px solid #ecf0f1;
        }
        
        .params-table tr:hover {
            background: #f8f9fa;
        }
        
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
        
        .copy-btn:hover {
            background: #2980b9;
        }
        
        .copy-btn.copied {
            background: #27ae60;
        }
        
        .json-key { color: #e06c75; }
        .json-string { color: #98c379; }
        .json-number { color: #d19a66; }
        .json-boolean { color: #56b6c2; }
        .json-null { color: #c678dd; }
        
        code {
            background: #f4f4f4;
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
        
        .note strong {
            color: #856404;
        }
        
        .example-tabs {
            display: flex;
            gap: 5px;
            margin-bottom: -1px;
        }
        
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
        
        .example-tab:hover {
            background: #bdc3c7;
        }
        
        .example-tab.active {
            background: #282c34;
            color: white;
        }
        
        .example-content {
            display: none;
        }
        
        .example-content.active {
            display: block;
        }
    </style>"""
    
    def _generate_sidebar(self) -> str:
        """Generate sidebar navigation."""
        sidebar_html = """<nav class="sidebar">
            <h1>OTIO Sync Protocol</h1>"""
        
        for category, schemas in self.categories.items():
            if schemas:
                sidebar_html += f"""
            <h2>{category} Events</h2>
            <ul>"""
                for schema in schemas:
                    anchor = self._make_anchor(schema.name)
                    sidebar_html += f"""
                <li><a href="#{anchor}">{schema.name}</a></li>"""
                sidebar_html += """
            </ul>"""
        
        sidebar_html += """
        </nav>"""
        return sidebar_html
    
    def _generate_content(self) -> str:
        """Generate main content area."""
        content = """<main class="content">
            <div class="section">
                <h2>Overview</h2>
                <p>This documentation describes the OTIO-based synchronized review messaging protocol. All events inherit from the base <code>SyncEvent</code> class and are serialized as OpenTimelineIO objects.</p>
                
                <div class="note">
                    <strong>Note:</strong> This documentation was automatically generated from the Python schema definitions. Examples are created from actual OTIO class instances defined in the configuration file.
                </div>
            </div>"""
        
        for category, schemas in self.categories.items():
            for schema in schemas:
                content += self._generate_schema_section(schema)
        
        content += """
        </main>"""
        return content
    
    def _generate_schema_section(self, schema: SchemaClass) -> str:
        """Generate documentation section for a single schema."""
        anchor = self._make_anchor(schema.name)
        
        section = f"""
            <div id="{anchor}" class="section">
                <h2>{schema.name}</h2>
                <span class="schema-label">{schema.schema_label}</span>
                
                <div class="description">{schema.description}</div>"""
        
        if schema.parameters:
            section += """
                <h3>Parameters</h3>
                <table class="params-table">
                    <thead>
                        <tr>
                            <th>Parameter</th>
                            <th>Type</th>
                            <th>Description</th>
                        </tr>
                    </thead>
                    <tbody>"""
            
            for param in schema.parameters:
                required_badge = "" if param.required else '<span class="required-badge">Optional</span>'
                section += f"""
                        <tr>
                            <td><code>{param.name}</code>{required_badge}</td>
                            <td><span class="type-badge">{param.type_hint}</span></td>
                            <td>{param.description}</td>
                        </tr>"""
            
            section += """
                    </tbody>
                </table>"""
        
        # Generate examples
        section += self._generate_examples_section(schema)
        
        section += """
            </div>"""
        
        return section
    

    def _generate_examples_section(self, schema: SchemaClass) -> str:
        """Generate examples section with tabs for multiple examples."""
        example_names = self.example_generator.get_available_examples(schema.name)
        
        if len(example_names) == 0:
            return ""
        
        section = """
                <h3>Example""" + ("s" if len(example_names) > 1 else "") + """</h3>"""
    
        # Generate tabs for multiple examples
        section += f"""
            <div class="example-tabs" id="tabs-{self._make_anchor(schema.name)}">"""
        
        for i, example_name in enumerate(example_names):
            active = "active" if i == 0 else ""
            section += f"""
                <button class="example-tab {active}" onclick="switchExampleTab(event, '{self._make_anchor(schema.name)}', '{example_name}')">{example_name.replace('_', ' ').title()}</button>"""
            section += f"""
                <button class="example-tab {active}" onclick="switchExampleTab(event, '{self._make_anchor(schema.name)}', '{example_name}_python')">{example_name.replace('_', ' ').title() + " (Python)"}</button>"""
        
        section += """
            </div>"""
        
        # Generate example content
        for i, example_name in enumerate(example_names):
            active = "active" if i == 0 else ""
            example_json = self.example_generator.generate_example(schema.name, example_name)
            
            if example_json:
                section += f"""
                <div id="{self._make_anchor(schema.name)}-{example_name}" class="example-content {active}">
                    <div class="code-block">
                        <button class="copy-btn" onclick="copyCode(this)">Copy</button>
                        <pre>{example_json}</pre>
                    </div>
                </div>"""
            # Generate Python example
            example_python = self.example_generator.generate_python_example(schema.name, example_name)
            if example_python:
                section += f"""
                <div id="{self._make_anchor(schema.name)}-{example_name}_python" class="example-content {active}">
                    <div class="code-block">
                        <button class="copy-btn" onclick="copyCode(this)">Copy</button>
                        <pre>{example_python}</pre>
                    </div>
                </div>"""
        
        return section
    
    def _make_anchor(self, name: str) -> str:
        """Convert class name to HTML anchor."""
        # Convert CamelCase to kebab-case
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1-\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1-\2', s1).lower()
    
    def _generate_javascript(self) -> str:
        """Generate JavaScript for interactivity."""
        return """<script>
        function copyCode(button) {
            const codeBlock = button.nextElementSibling;
            const text = codeBlock.textContent;
            
            navigator.clipboard.writeText(text).then(() => {
                button.textContent = 'Copied!';
                button.classList.add('copied');
                setTimeout(() => {
                    button.textContent = 'Copy';
                    button.classList.remove('copied');
                }, 2000);
            });
        }
        
        function switchExampleTab(event, schemaId, exampleName) {
            // Get all tabs and contents for this schema
            const tabContainer = document.getElementById('tabs-' + schemaId);
            const tabs = tabContainer.querySelectorAll('.example-tab');
            const contents = document.querySelectorAll('[id^="' + schemaId + '-"]');
            
            // Remove active class from all tabs and contents
            tabs.forEach(tab => tab.classList.remove('active'));
            contents.forEach(content => content.classList.remove('active'));
            
            // Add active class to clicked tab
            event.currentTarget.classList.add('active');
            
            // Show corresponding content
            const contentId = schemaId + '-' + exampleName;
            const content = document.getElementById(contentId);
            if (content) {
                content.classList.add('active');
            }
        }
        
        // Smooth scrolling for anchor links
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                const target = document.querySelector(this.getAttribute('href'));
                if (target) {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'start'
                    });
                }
            });
        });
    </script>"""


def generate_example_config(output_path: str):
    """Generate an example configuration file."""
    example_config = {
        "Play": {
            "default": {
                "value": True,
                "timestamp": "2025-01-31T16:14:00Z"
            },
            "pause": {
                "value": False,
                "timestamp": "2025-01-31T16:15:30Z"
            }
        },
        "SetCurrentFrame": {
            "default": {
                "time": {
                    "OTIO_SCHEMA": "RationalTime.1",
                    "value": 24.0,
                    "rate": 24.0
                },
                "timestamp": "2025-01-31T16:14:00Z"
            }
        },
        "NewPresenter": {
            "default": {
                "presenter_hash": "d3447b5cb61b41de73a2de39c4f06ab790e66e4cad81f7d449c0147a546244b5",
                "timestamp": "2025-01-31T16:14:00Z"
            }
        },
        "SyncPlayback": {
            "default": {
                "looping": True,
                "playing": False,
                "muted": False,
                "scrubbing": False,
                "current_time": {
                    "OTIO_SCHEMA": "RationalTime.1",
                    "value": 24.0,
                    "rate": 24.0
                },
                "playback_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 0.0,
                        "rate": 24.0
                    },
                    "duration": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 120.0,
                        "rate": 24.0
                    }
                },
                "output_bounds": {
                    "OTIO_SCHEMA": "Box2d.1",
                    "min": {
                        "x": -8.0,
                        "y": -4.5
                    },
                    "max": {
                        "x": 8.0,
                        "y": 4.5
                    }
                },
                "source": "/path/to/media.mov",
                "source_index": 0,
                "timestamp": "2025-01-31T16:14:00Z"
            }
        },
        "PaintStart": {
            "default": {
                "source_index": 0,
                "uuid": "foshdfbp5hdirt",
                "friendly_name": "Patrick Chevalier",
                "participant_hash": "d3447b5cb61b41de73a2de39c4f06ab790e66e4cad81f7d449c0147a546244b5",
                "rgba": [1.0, 1.0, 0.0, 1.0],
                "type": "color",
                "brush": "circle",
                "visible": True,
                "layer_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 0.0,
                        "rate": 24.0
                    },
                    "duration": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 1.0,
                        "rate": 24.0
                    }
                },
                "hold": False,
                "ghost": False,
                "ghost_before": 3,
                "ghost_after": 3,
                "timestamp": "2025-01-31T16:14:00Z"
            },
            "eraser": {
                "source_index": 0,
                "uuid": "eraser_uuid_123",
                "friendly_name": "Jane Doe",
                "participant_hash": "abc123def456",
                "rgba": [1.0, 1.0, 1.0, 1.0],
                "type": "erase",
                "brush": "circle",
                "visible": True,
                "layer_range": {
                    "OTIO_SCHEMA": "TimeRange.1",
                    "start_time": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 10.0,
                        "rate": 24.0
                    },
                    "duration": {
                        "OTIO_SCHEMA": "RationalTime.1",
                        "value": 1.0,
                        "rate": 24.0
                    }
                },
                "timestamp": "2025-01-31T16:15:00Z"
            }
        },
        "TextAnnotation": {
            "default": {
                "uuid": "text_annotation_uuid",
                "friendly_name": "John Smith",
                "position": [5.5, 2.0],
                "rgba": [1.0, 0.0, 0.0, 1.0],
                "text": "Fix this shot",
                "spacing": 1.2,
                "font_size": 24.0,
                "scale": 1.0,
                "rotation": 0.0,
                "font": "Arial",
                "timestamp": "2025-01-31T16:14:00Z"
            }
        }
    }
    
    with open(output_path, 'w') as f:
        if output_path.endswith('.yaml') or output_path.endswith('.yml'):
            yaml.dump(example_config, f, default_flow_style=False, sort_keys=False)
        else:
            json.dump(example_config, f, indent=2)
    
    print(f"Example configuration generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML documentation from OTIO SyncEvent schema definitions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate docs with custom examples
  python generate_docs.py -i SyncEvent.py -c examples.yaml -o docs.html
  
  # Generate example config file
  python generate_docs.py --generate-config examples.yaml
  
  # Generate docs without custom examples (uses defaults)
  python generate_docs.py -i SyncEvent.py -o docs.html
        """
    )
    
    parser.add_argument(
        "--input", "-i",
        help="Input Python file containing schema definitions"
    )
    parser.add_argument(
        "--config", "-c",
        help="YAML or JSON configuration file with example instances"
    )
    parser.add_argument(
        "--output", "-o",
        default="otio_sync_docs.html",
        help="Output HTML file path (default: otio_sync_docs.html)"
    )
    parser.add_argument(
        "--generate-config",
        metavar="OUTPUT",
        help="Generate an example configuration file and exit"
    )
    
    args = parser.parse_args()
    
    # Handle config generation
    if args.generate_config:
        generate_example_config(args.generate_config)
        return
    
    # Validate required arguments
    if not args.input:
        parser.error("--input is required (unless using --generate-config)")
    
    # Parse schemas
    print(f"Parsing schemas from {args.input}...")
    schema_parser = OTIOSchemaParser(args.input, args.config)
    schemas = schema_parser.parse()
    print(f"Found {len(schemas)} schemas")

    # Get the categories
    print(f"Categories found: {list(schema_parser.categories.keys())}")
    categories = schema_parser.categories.keys()
    
    # Create example generator
    print(f"Loading examples...")
    example_generator = ExampleGenerator(args.input, args.config)
    if args.config:
        print(f"Loaded examples from {args.config}")
    else:
        print("No config provided - examples will use default values")
    
    # Generate documentation
    print(f"Generating HTML documentation...")
    generator = HTMLDocGenerator(schemas, categories, example_generator)
    generator.generate(args.output)
    
    print("Done!")


if __name__ == "__main__":
    main()