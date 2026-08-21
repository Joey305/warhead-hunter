from __future__ import annotations

import re


_TWO_LETTER_ELEMENTS = {
    "CL": "Cl",
    "BR": "Br",
    "SI": "Si",
    "SE": "Se",
    "MG": "Mg",
    "ZN": "Zn",
    "FE": "Fe",
    "CU": "Cu",
    "MN": "Mn",
    "NI": "Ni",
    "HG": "Hg",
    "PB": "Pb",
    "AG": "Ag",
    "AU": "Au",
    "AL": "Al",
    "LI": "Li",
}
_ORGANIC_FIRST_LETTERS = set("HCNOSPFIB") | {"I"}
_HYDROGEN_SYMBOLS = {"H", "D", "T"}


def normalize_element_symbol(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""

    match = re.search(r"([A-Za-z]{1,2})", text)
    if not match:
        return ""

    token = match.group(1)
    if len(token) == 1:
        return token.upper()
    return token[0].upper() + token[1].lower()


def is_hydrogen_symbol(symbol) -> bool:
    return normalize_element_symbol(symbol).upper() in _HYDROGEN_SYMBOLS


def infer_element_from_atom_name(atom_name: str) -> str:
    if atom_name is None:
        return "C"

    text = str(atom_name).strip()
    text = re.sub(r"^[^A-Za-z]+", "", text)
    text = re.sub(r"^[0-9]+", "", text)

    match = re.match(r"^([A-Za-z]{1,2})", text)
    if not match:
        return "C"

    token = match.group(1).upper()

    # In ligand atom naming, a leading H almost always means a hydrogen label
    # such as HG2/HB3/HN1 rather than mercury/holmium.
    if token.startswith("H"):
        return "H"

    if token in _TWO_LETTER_ELEMENTS:
        if token.startswith("C") and len(token) == 2 and token not in {"CL"}:
            return "C"
        return _TWO_LETTER_ELEMENTS[token]

    if token[0] in _ORGANIC_FIRST_LETTERS:
        return token[0]

    if len(token) == 2:
        return token[0] + token[1].lower()
    return token[0]


def element_from_atom_record(atom_name: str, element=None) -> str:
    explicit = normalize_element_symbol(element)
    if explicit:
        return "H" if is_hydrogen_symbol(explicit) else explicit
    return infer_element_from_atom_name(atom_name)


def mol_graph_atom_indices(mol) -> set[int]:
    return {atom.GetIdx() for atom in mol.GetAtoms() if atom.GetAtomicNum() != 1}
