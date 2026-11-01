"""The licence texts of the libraries OR-Tools' wheel bundles (`packaging/desktop/licenses/`),
which the desktop app's THIRD-PARTY-LICENSES.txt reproduces after the spec's `BUNDLED`
(docs/reference/desktop.md, **The bundled libraries' licences**): a file per entry of `BUNDLED`,
in its order, each a header naming the entry's libraries and licence, then the text. The spec
runs only under PyInstaller, so `BUNDLED` is read from its source."""

import ast
from pathlib import Path

DESKTOP = Path(__file__).parents[2] / "packaging" / "desktop"
FIELDS = ["Library", "Version", "Licence", "Copyright", "Source"]


def bundled() -> list[tuple[str, str]]:
    """The spec's `BUNDLED` entries, each its libraries and their licence."""
    spec = ast.parse((DESKTOP / "worst-route-desktop.spec").read_text())
    (text,) = [
        ast.literal_eval(node.value)
        for node in spec.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "BUNDLED"
    ]
    entries = [line[2:].partition(": ") for line in text.splitlines() if line.startswith("- ")]
    return [(libraries, licence) for libraries, _, licence in entries]


def read(path: Path) -> tuple[list[tuple[str, str]], str]:
    """A licence file's header, its fields in order (a field's further lines indented under
    it), and its text after the blank line."""
    head, _, body = path.read_text(encoding="utf-8").partition("\n\n")
    fields: list[tuple[str, str]] = []
    for line in head.splitlines():
        if line.startswith(" ") and fields:
            key, value = fields[-1]
            fields[-1] = (key, f"{value} {line.strip()}")
        else:
            key, _, value = line.partition(":")
            fields.append((key, value.strip()))
    return fields, body


def test_every_library_bundled_has_its_text_in_the_order_named() -> None:
    headers = [dict(read(path)[0]) for path in sorted((DESKTOP / "licenses").glob("*.txt"))]
    assert bundled()
    assert [(header["Library"], header["Licence"]) for header in headers] == bundled()


def test_each_text_has_its_header_and_a_body() -> None:
    for path in sorted((DESKTOP / "licenses").glob("*.txt")):
        fields, body = read(path)
        assert [key for key, _ in fields] == FIELDS, path.name
        assert all(value for _, value in fields), path.name
        assert "https://" in dict(fields)["Source"], path.name
        assert len(body.split()) > 50, path.name  # a licence, not a stub or an error page
