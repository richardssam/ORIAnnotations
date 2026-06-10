#!/usr/bin/env python3
"""Transport-layer protocol message documentation generator.

Generates a standalone HTML page documenting every registered
:class:`~otio_sync_core.protocol_messages.ProtocolMessage`, mirroring how
``otio_doc_generator.py`` documents the OTIO ``SyncEvent`` layer.

Unlike the OTIO generator (which AST-parses its source), this imports the
``protocol_messages`` module directly and introspects the registry, since the
message module is self-contained pure-Python and cheap to import.

Categories and example payloads come from an optional YAML side-file
(``protocol_messages_config.yml``); messages absent from the side-file are
still documented using their class-derived schema, event, and fields.

Usage:
    python protocol_doc_generator.py [--config protocol_messages_config.yml] \
        [--output protocol_messages.html]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a dev dependency
    yaml = None

# Make the otio_sync_core package importable regardless of CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "python"))

from otio_sync_core import protocol_messages as pm  # noqa: E402


def load_config(config_path: "str | None") -> dict:
    """Load the categories/examples side-file, or return ``{}`` when absent.

    :param config_path: Path to a YAML/JSON side-file, or ``None``.
    :returns: Parsed config dict keyed by message class name.
    """
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r") as f:
        if path.suffix in (".yaml", ".yml"):
            if yaml is None:
                raise RuntimeError("PyYAML is required to read a YAML config side-file")
            return yaml.safe_load(f) or {}
        return json.load(f)


def collect_messages(config: dict) -> list[dict]:
    """Introspect the registry into a list of message description dicts.

    :param config: Parsed side-file config (class-name keyed).
    :returns: One dict per registered message with name, schema, event,
        description, fields, category, and examples.
    """
    messages = []
    for (schema, event), cls in pm.registered_messages().items():
        entry = config.get(cls.__name__, {}) or {}
        category = entry.get("_category", "Uncategorized")
        examples = {k: v for k, v in entry.items() if k != "_category"}
        description = (cls.__doc__ or "").strip().split("\n\n")[0].strip()
        messages.append(
            {
                "name": cls.__name__,
                "schema": schema,
                "event": event,
                "description": description,
                "fields": cls.doc_fields(),
                "category": category,
                "examples": examples,
            }
        )
    # Stable order: category, then schema, then event.
    messages.sort(key=lambda m: (m["category"], m["schema"], m["event"]))
    return messages


def render_html(messages: list[dict]) -> str:
    """Render the message list into a standalone HTML document.

    :param messages: Output of :func:`collect_messages`.
    :returns: Complete HTML document string.
    """
    def esc(s):
        return html.escape(str(s))

    # Group by category preserving sorted order.
    categories: dict[str, list[dict]] = {}
    for m in messages:
        categories.setdefault(m["category"], []).append(m)

    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>OTIO Sync — Protocol Messages</title>",
        "<style>",
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;"
        "background:#0f1115;color:#e6e6e6;line-height:1.5}",
        ".wrap{max-width:920px;margin:0 auto;padding:2rem}",
        "h1{font-size:1.8rem}h2{margin-top:2.5rem;border-bottom:1px solid #2a2f3a;"
        "padding-bottom:.3rem;color:#9ecbff}",
        ".msg{background:#161a22;border:1px solid #2a2f3a;border-radius:8px;"
        "padding:1rem 1.25rem;margin:1rem 0}",
        ".msg h3{margin:.2rem 0;font-size:1.2rem}",
        ".badge{display:inline-block;font-size:.75rem;font-family:monospace;"
        "background:#243047;color:#9ecbff;padding:.1rem .5rem;border-radius:4px;"
        "margin-right:.4rem}",
        ".desc{color:#b9c0cc;margin:.5rem 0}",
        "table{border-collapse:collapse;width:100%;margin:.5rem 0;font-size:.9rem}",
        "th,td{text-align:left;padding:.35rem .6rem;border-bottom:1px solid #232834;"
        "vertical-align:top}",
        "th{color:#8a93a3;font-weight:600}",
        "code{font-family:monospace;color:#e3b341}",
        "pre{background:#0b0d12;border:1px solid #232834;border-radius:6px;"
        "padding:.75rem;overflow-x:auto;font-size:.82rem}",
        ".toc a{color:#9ecbff;text-decoration:none;margin-right:1rem}",
        "</style></head><body><div class='wrap'>",
        "<h1>OTIO Sync — Protocol Messages</h1>",
        "<p class='desc'>Transport-layer messages exchanged over the sync "
        "network. Generated from "
        "<code>otio_sync_core.protocol_messages</code>.</p>",
    ]

    # Table of contents.
    parts.append("<p class='toc'>")
    for cat in categories:
        parts.append(f"<a href='#{esc(cat.replace(' ', '-'))}'>{esc(cat)}</a>")
    parts.append("</p>")

    for cat, msgs in categories.items():
        parts.append(f"<h2 id='{esc(cat.replace(' ', '-'))}'>{esc(cat)}</h2>")
        for m in msgs:
            parts.append("<div class='msg'>")
            parts.append(f"<h3>{esc(m['name'])}</h3>")
            parts.append(
                f"<span class='badge'>schema: {esc(m['schema'])}</span>"
                f"<span class='badge'>event: {esc(m['event'])}</span>"
            )
            if m["description"]:
                parts.append(f"<p class='desc'>{esc(m['description'])}</p>")
            if m["fields"]:
                parts.append("<table><tr><th>Field</th><th>Type</th>"
                             "<th>Description</th></tr>")
                for name, ftype, doc in m["fields"]:
                    parts.append(
                        f"<tr><td><code>{esc(name)}</code></td>"
                        f"<td>{esc(ftype)}</td><td>{esc(doc)}</td></tr>"
                    )
                parts.append("</table>")
            for ex_name, ex_payload in m["examples"].items():
                label = "Example" if ex_name == "default" else f"Example: {ex_name}"
                parts.append(f"<p class='desc'><strong>{esc(label)}</strong></p>")
                parts.append(f"<pre>{esc(json.dumps(ex_payload, indent=2))}</pre>")
            parts.append("</div>")

    parts.append("</div></body></html>")
    return "\n".join(parts)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "protocol_messages_config.yml"),
        help="Path to the categories/examples YAML side-file.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).parent / "protocol_messages.html"),
        help="Output HTML path.",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    messages = collect_messages(config)
    out = render_html(messages)
    with open(args.output, "w") as f:
        f.write(out)
    print(f"Wrote {len(messages)} protocol messages to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
