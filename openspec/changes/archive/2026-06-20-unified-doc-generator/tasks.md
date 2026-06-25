## 1. Restructure config.yml

- [x] 1.1 Add `meta:` section to `docs/config.yml` with `title`, `otio_input`, and `output` keys
- [x] 1.2 Wrap all existing class entries in `docs/config.yml` under an `otio_events:` key
- [x] 1.3 Add `protocol_messages:` section to `docs/config.yml` with all entries from `docs/protocol_messages_config.yml`
- [x] 1.4 Verify the merged config is valid YAML and categories are preserved for both sections

## 2. Create doc_generator.py — config loading and data model

- [x] 2.1 Create `docs/doc_generator.py` with the unified `SchemaClass` dataclass (add `source_type: str` and `event: str = ""` fields to existing definition)
- [x] 2.2 Implement `load_config(path)` that parses `meta`, `otio_events`, and `protocol_messages` from the unified config file
- [x] 2.3 Port `OTIOSchemaParser` from `otio_doc_generator.py` unchanged; update it to read its examples config from the `otio_events` sub-dict rather than a separate file

## 3. Protocol message collection

- [x] 3.1 Port `collect_messages()` from `protocol_doc_generator.py`; adapt it to build `SchemaClass` instances (with `source_type="protocol"`) instead of plain dicts
- [x] 3.2 Implement `generate_protocol_python_example(class_name, params)` that produces a `# Protocol Message (transport layer)\nfrom otio_sync_core.protocol_messages import <ClassName>\n\nmsg = <ClassName>(...)` snippet, formatted with `black`

## 4. HTML generator — unified output

- [x] 4.1 Update `HTMLDocGenerator.__init__` to accept separate `otio_schemas` and `protocol_schemas` lists plus their respective category orders
- [x] 4.2 Update `_generate_sidebar()` to render two labelled sidebar sections (OTIO SyncEvents first, Protocol Messages second), each with their own category groupings
- [x] 4.3 Update `_generate_content()` to render the introduction section first (from rendered Markdown or default paragraph), then OTIO sections, then protocol sections
- [x] 4.4 Implement `_render_introduction(meta)` using `mistune.create_markdown()` to convert the `.md` file referenced by `meta.introduction` to HTML; return default paragraph if key absent
- [x] 4.5 Update `_generate_schema_section()` to conditionally show an `event` badge alongside the `schema_label` badge when `schema.source_type == "protocol"`
- [x] 4.6 Update `_generate_examples_section()` to call `generate_protocol_python_example()` for protocol schemas and the existing `generate_python_example()` for OTIO schemas

## 5. CLI and entry point

- [x] 5.1 Replace `main()` argument parser: remove `--input`, keep `--config` (default `docs/config.yml`) and `--output` (overrides `meta.output`)
- [x] 5.2 Wire `main()` to load config, parse OTIO schemas, collect protocol messages, and call `HTMLDocGenerator` with combined data
- [x] 5.3 Add graceful error if `otio_sync_core` cannot be imported (print clear message, exit non-zero)

## 6. Update Makefile and retire old files

- [x] 6.1 Update `docs/Makefile` to call `python doc_generator.py --config config.yml` instead of the two old generators
- [x] 6.2 Delete `docs/otio_doc_generator.py`
- [x] 6.3 Delete `docs/protocol_doc_generator.py`
- [x] 6.4 Delete `docs/protocol_messages_config.yml`

## 7. Verify

- [x] 7.1 Run `python docs/doc_generator.py --config docs/config.yml --output docs/otio_sync_docs.html` and confirm the output file is generated without errors
- [x] 7.2 Open the HTML and verify the sidebar shows two sections (OTIO SyncEvents, Protocol Messages) each with correct category groupings
- [x] 7.3 Verify at least one OTIO event and one protocol message each have JSON and Python tabs with the correct layer comment
- [x] 7.4 If `meta.introduction` is set, verify the first content section contains rendered Markdown
