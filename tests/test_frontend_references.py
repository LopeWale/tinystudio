"""Grammar-level reference checks: no app import, Node or mutable frontend assets."""
from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('isolated_frontend_references', Path(__file__).resolve().parents[1] / 'tinystudio/frontend_references.py')
references = importlib.util.module_from_spec(spec)
spec.loader.exec_module(references)


@pytest.mark.parametrize('source', [
    "obj.return / import('outside-package') /2;",
    "obj.if() / import('outside-package') /2;",
    "const x=1 % {} / import('outside-package') /2;",
    "const value=this.#return / import('outside-package') /2;",
])
def test_operator_and_property_context_cannot_hide_an_import(source):
    assert references.module_references(source) == ['outside-package']


@pytest.mark.parametrize('source', [
    'const value=1;export{value as in};',
    'import{in as value}from"./dependency.js";',
])
def test_reserved_module_name_compatibility_reparses_without_modifying_assets(source):
    original = source.encode()
    assert not references._parse(original).root_node.has_error
    assert source.encode() == original


@pytest.mark.parametrize('source', [
    'export{value as in}from"outside-package";',
    'import{in as value}from"outside-package";',
    'const value=1;export{value as in};import("outside-package");',
])
def test_reserved_module_names_never_hide_module_source(source):
    assert references.module_references(source) == ['outside-package']


@pytest.mark.parametrize('source', [
    'export{,as in};',
    'import{in as ,value}from"./dependency.js";',
    'export{value as in};const broken=;',
    'import{in as value}from"./dependency.js";import("outside-package"',
])
def test_no_general_error_node_or_malformed_surrounding_code_bypass(source):
    with pytest.raises(references.ModuleReferenceError, match='syntax cannot be verified'):
        references.module_references(source)


def test_missing_parser_fails_actionably_without_installer(monkeypatch):
    original = builtins.__import__
    def importing(name, *args, **kwargs):
        if name == 'tree_sitter':
            raise ImportError('controlled missing parser')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', importing)
    with pytest.raises(references.ModuleReferenceError, match='requires tree-sitter==0.25.2.*install the declared'):
        references.module_references('import "outside-package";')


def test_nested_template_expressions_and_escaped_module_literals_are_inspected():
    assert references.module_references(r'const value=`outer ${`inner ${import("\x6futside-package")}`}`;') == ['outside-package']


def test_regex_literals_property_calls_and_import_meta_have_no_module_reference():
    source = r'''const pattern=/import("outside-package")/;obj.import("outside-package");import.meta.url;'''
    assert references.module_references(source) == []


@pytest.mark.parametrize('source,expected', [
    ('import(("https://outside.invalid/module.js"));', 'https://outside.invalid/module.js'),
    ('import((/*comment*/("../private.js")));', '../private.js'),
    ('import(((`./dependency.js`)));', './dependency.js'),
    ('import(("https://outside.invalid/")+name);', 'https://outside.invalid/'),
    ('import((("https://outside.invalid/")+name));', 'https://outside.invalid/'),
])
def test_transparent_parentheses_preserve_module_reference(source, expected):
    assert references.module_references(source) == [expected]


def test_parenthesized_computed_template_fails_closed():
    with pytest.raises(references.ModuleReferenceError, match='Computed template'):
        references.module_references('import(((`https://${host}/module.js`)));')


def test_parenthesized_opaque_expression_retains_manifest_policy():
    assert references.module_references('import(((route.module)));') == []
