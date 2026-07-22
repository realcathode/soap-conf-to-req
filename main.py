import xml.etree.ElementTree as ET
from xml.dom import minidom
import os
import sys
import yaml
import argparse

NS = {
    'wsdl': 'http://schemas.xmlsoap.org/wsdl/',
    'xs': 'http://www.w3.org/2001/XMLSchema'
}

def strip_ns(tag: str) -> str:
    return tag.split('}')[-1] if '}' in tag else tag

def get_base_type(type_str: str) -> str:
    return type_str.split(':')[-1] if ':' in type_str else type_str

def build_yaml_lookup(yaml_file: str) -> dict:
    """Indexes the OpenAPI YAML into a flat, case-insensitive dictionary."""
    if not yaml_file or not os.path.exists(yaml_file):
        return {}

    with open(yaml_file, 'r', encoding='utf-8') as f:
        try:
            openapi_spec = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(f"Error parsing YAML: {exc}")
            return {}

    lookup = {}
    schemas = openapi_spec.get('components', {}).get('schemas', {})

    for schema_name, schema_def in schemas.items():
        lookup[schema_name.lower()] = schema_def
        if 'properties' in schema_def:
            for prop_name, prop_def in schema_def['properties'].items():
                lookup[prop_name.lower()] = prop_def

    return lookup

def generate_value(xsd_type: str, elem_name: str, yaml_lookup: dict) -> str:
    """Generates data prioritizing YAML examples, then YAML types, then XSD fallbacks."""
    yaml_def = yaml_lookup.get(elem_name.lower(), {})

    if 'example' in yaml_def:
        return str(yaml_def['example'])

    effective_type = yaml_def.get('type', xsd_type).lower()

    if 'string' in effective_type:
        val = f"sample_{elem_name}"
        max_len = yaml_def.get('maxLength')
        if max_len and len(val) > max_len:
            return val[:max_len]
        return val

    elif 'int' in effective_type or 'long' in effective_type or 'integer' in effective_type:
        return "12345"
    elif 'decimal' in effective_type or 'float' in effective_type or 'double' in effective_type or 'number' in effective_type:
        return "123.45"
    elif 'boolean' in effective_type:
        return "true"
    elif 'date' in effective_type or 'datetime' in effective_type:
        return "2026-07-21T12:00:00Z"
    elif 'uuid' in effective_type:
        return "123e4567-e89b-12d3-a456-426614174000"
    else:
        return f"value_for_{elem_name}"

def find_complex_type(schema: ET.Element, type_name: str) -> ET.Element:
    return schema.find(f".//xs:complexType[@name='{type_name}']", NS)

def find_simple_type(schema: ET.Element, type_name: str) -> ET.Element:
    return schema.find(f".//xs:simpleType[@name='{type_name}']", NS)

def find_global_element(schema: ET.Element, elem_name: str) -> ET.Element:
    return schema.find(f"./xs:element[@name='{elem_name}']", NS)

def parse_simple_type(schema: ET.Element, simple_type: ET.Element, elem_name: str, yaml_lookup: dict) -> str:
    restriction = simple_type.find(f"./xs:restriction", NS)
    if restriction is not None:
        base_type = get_base_type(restriction.get('base', 'string'))

        enums = restriction.findall(f"./xs:enumeration", NS)
        if enums:
            return enums[0].get('value')

        val = generate_value(base_type, elem_name, yaml_lookup)

        max_len_node = restriction.find(f"./xs:maxLength", NS)
        if max_len_node is not None:
            max_len = int(max_len_node.get('value'))
            if len(val) > max_len:
                val = val[:max_len]

        return val

    return generate_value("string", elem_name, yaml_lookup)

def walk_schema_nodes(schema: ET.Element, current_xsd_node: ET.Element, parent_xml: ET.Element, ancestors: set, yaml_lookup: dict):
    for child in current_xsd_node:
        tag = strip_ns(child.tag)

        if tag == 'element':
            process_element(schema, child, parent_xml, ancestors, yaml_lookup)
        elif tag in ['sequence', 'choice', 'all', 'complexContent', 'simpleContent']:
            walk_schema_nodes(schema, child, parent_xml, ancestors, yaml_lookup)
        elif tag == 'extension':
            base_type = get_base_type(child.get('base', ''))
            if base_type and base_type not in ancestors:
                base_ct = find_complex_type(schema, base_type)
                if base_ct is not None:
                    walk_schema_nodes(schema, base_ct, parent_xml, ancestors | {base_type}, yaml_lookup)
            walk_schema_nodes(schema, child, parent_xml, ancestors, yaml_lookup)

def process_element(schema: ET.Element, xsd_elem: ET.Element, parent_xml: ET.Element, ancestors: set, yaml_lookup: dict):
    if ancestors is None:
        ancestors = set()

    elem_name = xsd_elem.get('name')

    if not elem_name:
        ref = xsd_elem.get('ref')
        if ref:
            elem_name = get_base_type(ref)
            global_elem = find_global_element(schema, elem_name)
            if global_elem is not None:
                process_element(schema, global_elem, parent_xml, ancestors, yaml_lookup)
                return
            else:
                xml_node = ET.SubElement(parent_xml, elem_name)
                xml_node.text = generate_value("string", elem_name, yaml_lookup)
                return
        return

    xml_node = ET.SubElement(parent_xml, elem_name)

    fixed_val = xsd_elem.get('fixed') or xsd_elem.get('default')
    if fixed_val:
        xml_node.text = fixed_val
        return

    type_attr = xsd_elem.get('type')

    if type_attr:
        base_type = get_base_type(type_attr)

        if type_attr.startswith('xs:') or type_attr.startswith('xsd:') or base_type.lower() in ['string', 'int', 'integer', 'long', 'boolean', 'decimal', 'float', 'double', 'date', 'datetime', 'base64binary']:
            xml_node.text = generate_value(base_type, elem_name, yaml_lookup)
        else:
            if base_type in ancestors:
                xml_node.append(ET.Comment(f" Cyclic reference to '{base_type}' aborted "))
                return

            complex_type = find_complex_type(schema, base_type)
            if complex_type is not None:
                walk_schema_nodes(schema, complex_type, xml_node, ancestors | {base_type}, yaml_lookup)
            else:
                simple_type = find_simple_type(schema, base_type)
                if simple_type is not None:
                    xml_node.text = parse_simple_type(schema, simple_type, elem_name, yaml_lookup)
                else:
                    xml_node.text = generate_value("string", elem_name, yaml_lookup)

    else:
        inline_complex = xsd_elem.find(f"./xs:complexType", NS)
        if inline_complex is not None:
            walk_schema_nodes(schema, inline_complex, xml_node, ancestors, yaml_lookup)
            return

        inline_simple = xsd_elem.find(f"./xs:simpleType", NS)
        if inline_simple is not None:
            xml_node.text = parse_simple_type(schema, inline_simple, elem_name, yaml_lookup)
            return

def prettify_xml(elem: ET.Element) -> str:
    rough_string = ET.tostring(elem, 'utf-8')
    reparsed = minidom.parseString(rough_string)
    return '\n'.join([line for line in reparsed.toprettyxml(indent="  ").split('\n') if line.strip()])

# --- CLI and Interactive Menu Helpers ---

def choose_file_interactive(label: str, extensions: list, optional: bool = False) -> str:
    """Scans the directory for files matching the given extensions/keywords, sorts them alphabetically, and returns the choice."""
    files = sorted([
        f for f in os.listdir('.')
        if os.path.isfile(f) and any(ext in f.lower() for ext in extensions)
    ])

    if not files:
        if optional:
            return ""
        print(f"Error: No {label} files found in the current directory.")
        sys.exit(1)

    print(f"\nSelect a {label} file:")
    if optional:
        print("[N] None (Skip)")

    for i, f in enumerate(files):
        print(f"[{i}] {f}")

    while True:
        choice = input(f"Select file index (0-{len(files)-1}){ ' or N to skip' if optional else ''}: ").strip()
        if optional and choice.lower() == 'n':
            return ""
        if choice.isdigit() and 0 <= int(choice) < len(files):
            return files[int(choice)]
        print("Invalid selection.")

def main():
    parser = argparse.ArgumentParser(description="Generate a mock SOAP payload from a WSDL and OpenAPI YAML file.")
    parser.add_argument("--wsdl", type=str, help="Path to the WSDL file")
    parser.add_argument("--yaml", type=str, help="Path to the OpenAPI YAML file (optional)")
    parser.add_argument("--num", type=str, help="File number for the output (e.g., 25)")
    parser.add_argument("--desc", type=str, help="Short description for the output file name")

    args = parser.parse_args()

    wsdl_file = args.wsdl
    yaml_file = args.yaml

    if not wsdl_file:
        wsdl_file = choose_file_interactive("WSDL", ["wsdl", ".xml"])
        if not yaml_file:
            yaml_file = choose_file_interactive("YAML", ["yaml", ".yml"], optional=True)

    if not os.path.exists(wsdl_file):
        print(f"Error: WSDL File '{wsdl_file}' not found.")
        return

    yaml_lookup = build_yaml_lookup(yaml_file)
    if yaml_lookup:
        print(f"Loaded YAML dictionary with {len(yaml_lookup)} indexed properties.")

    try:
        ET.register_namespace('soap', "http://schemas.xmlsoap.org/soap/envelope/")
        tree = ET.parse(wsdl_file)
        root = tree.getroot()
    except Exception as e:
        print(f"Error parsing WSDL XML: {e}")
        return

    schema = root.find(f".//wsdl:types/xs:schema", NS)
    if schema is None:
        print("Error: Could not find <types><schema> block in the WSDL.")
        return

    global_elements = []
    seen_names = set()

    for elem in schema.findall(f"./xs:element", NS):
        name = elem.get('name')
        if name and name not in seen_names:
            seen_names.add(name)
            global_elements.append(elem)

    if not global_elements:
        print("No root elements found in the schema.")
        return

    # Sort root elements alphabetically by name
    global_elements.sort(key=lambda x: x.get('name', '').lower())

    print("\nFound the following root elements (operations/messages):")
    for i, elem in enumerate(global_elements):
        print(f"[{i}] {elem.get('name')}")

    choice = input(f"\nSelect the root element to generate a payload for (0-{len(global_elements)-1}): ").strip()
    if not choice.isdigit() or not (0 <= int(choice) < len(global_elements)):
        print("Invalid selection.")
        return

    selected_element = global_elements[int(choice)]

    target_num = args.num
    if not target_num:
        target_num = input("\nEnter the file number for the output (e.g., 25): ").strip()
        if not target_num.isdigit():
            print("Error: Input must be a number.")
            return

    description = args.desc
    if not description:
        description = input("Enter a short description for the file name (e.g., payload): ").strip()

    soap_envelope = ET.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope")
    soap_body = ET.SubElement(soap_envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")

    process_element(schema, selected_element, soap_body, set(), yaml_lookup)

    xml_output = prettify_xml(soap_envelope)

    output_filename = f"input_{target_num}_{description}.xml"
    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(xml_output)

    print(f"\nSUCCESS: Generated mock SOAP payload saved to '{output_filename}'")

if __name__ == "__main__":
    main()
