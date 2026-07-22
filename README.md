> This was created primarily for my own convenience to speed up generating mock SOAP payloads during development and testing, using type information and ready examples from a yaml configuration and field structures from a wsdl. It has worked well for my use cases, but it hasn't been exhaustively tested and may still contain small bugs or edge cases. Feel free to use it, modify it, and report or fix any issues you encounter.

## Features
Generate sample SOAP request payloads directly from a WSDL. Optionally improve generated values using an OpenAPI YAML specification so that fields use realistic examples instead of generic placeholder data.

Supports:
  - complex/simple/inline types
  - enumerations
  - referenced elements
- Prevents infinite recursion from cyclic references
- Uses OpenAPI YAML examples when available
- Respects enum values, fixed/default values
- Interactive menu for selecting WSDL, YAML(optional), SOAP operation
- Pretty-printed XML output

<img width="1477" height="1312" alt="image" src="https://github.com/user-attachments/assets/42170429-76c6-4a30-a1dd-1d3164adab6e" />
