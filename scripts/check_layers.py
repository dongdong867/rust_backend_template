"""Conservative lexical architecture lint; run with Python 3 from the repo root.

This is not a Rust compiler: forbidden identifiers are rejected even in inactive
code, and aliases are caught at their import, not resolved across modules.
Cargo dependency declarations are scanned lexically (including renamed,
workspace, target-specific and table-form dependencies), without third parties.
"""
import pathlib
import re


def rust_tokens(source):
    """Yield (token, line), skipping comments/literals but preserving lifetimes."""
    i, line = 0, 1
    while i < len(source):
        start = i
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        elif source.startswith("/*", i):
            i += 2
            depth = 1
            while i < len(source) and depth:
                if source.startswith("/*", i):
                    depth += 1
                    i += 2
                elif source.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
        else:
            raw = re.match(r'(?:br|cr|r)(#*)"', source[i:])
            char = re.match(r"'(?:\\(?:u\{[0-9a-fA-F_]+\}|x[0-9a-fA-F]{2}|.)|[^'\\\n])'", source[i:])
            if raw:
                closing = '"' + raw.group(1)
                end = source.find(closing, i + raw.end())
                i = len(source) if end < 0 else end + len(closing)
            elif source[i] == '"':
                i += 1
                while i < len(source):
                    if source[i] == "\\":
                        i += 2
                    elif source[i] == '"':
                        i += 1
                        break
                    else:
                        i += 1
            elif char:
                i += char.end()
            else:
                identifier = re.match(r"(?:r#)?[A-Za-z_][A-Za-z_0-9]*", source[i:])
                if identifier:
                    token = identifier.group()
                    yield token[2:] if token.startswith("r#") else token, line
                    i += identifier.end()
                else:
                    if not source[i].isspace():
                        yield source[i], line
                    i += 1
        line += source[start:i].count("\n")


def is_actix(name):
    return name == "actix" or name.startswith(("actix_", "actix-"))


def source_violations(path, inner_layer):
    tokens = list(rust_tokens(path.read_text(encoding="utf-8")))
    for index, (token, line) in enumerate(tokens):
        violation = None
        if is_actix(token):
            violation = "Actix is forbidden in business feature crates"
        elif inner_layer:
            if token == "sqlx":
                violation = "SQLx is forbidden in domain/application"
            elif token in ("environment", "dotenvy"):
                violation = "environment access is forbidden in domain/application"
            elif token == "env" or (
                token == "option_env" and index + 1 < len(tokens)
                and tokens[index + 1][0] == "!"
            ):
                # Reject the env identifier conservatively. This also catches
                # grouped std imports and aliases without a Rust use-tree parser.
                violation = "environment access is forbidden in domain/application"
        if violation:
            yield line, violation


TOML_TOKEN = re.compile(
    r'"""(?:\\.|[^\\])*?"""|\'\'\'.*?\'\'\'|'
    r'"(?:\\.|[^"\\])*"|\'[^\'\n]*\'|#[^\n]*|\n|'
    r'[A-Za-z0-9_-]+|[^\s]', re.MULTILINE | re.DOTALL
)


def dependencies(path):
    """Return dependency names, package aliases, workspace flags and lines.

    Strings are retained as single tokens so comments and metadata cannot invent
    dependency declarations. Newlines inside inline tables are accepted too.
    """
    if not path.exists():
        return []
    tokens = []
    line = 1
    for match in TOML_TOKEN.finditer(path.read_text(encoding="utf-8")):
        token = match.group()
        if not token.startswith("#"):
            tokens.append((token.strip("\"'"), line))
        line += token.count("\n")
    records, section, i = {}, [], 0
    kinds = {"dependencies", "dev-dependencies", "build-dependencies"}
    while i < len(tokens):
        token, line = tokens[i]
        if token == "[":
            i += 1
            section = []
            while i < len(tokens) and tokens[i][0] != "]":
                if tokens[i][0] not in (".", "\n"):
                    section.append(tokens[i][0])
                i += 1
            i += 1
            continue
        if token == "\n":
            i += 1
            continue
        statement, depth = [], 0
        while i < len(tokens):
            item = tokens[i][0]
            if item == "\n" and depth == 0:
                break
            depth += item in ("{", "[")
            depth -= item in ("}", "]")
            statement.append(item)
            i += 1
        kind = next((n for n, part in enumerate(section) if part in kinds), None)
        if kind is None or "=" not in statement:
            continue
        equal = statement.index("=")
        keys = [part for part in statement[:equal] if part != "."]
        values = statement[equal + 1:]
        if not keys:
            continue
        suffix = section[kind + 1:]
        name = suffix[0] if suffix else keys[0]
        # An alias can denote different packages in different target/dev tables.
        scope = tuple(section[:kind + 1])
        record = records.setdefault((scope, name), {
            "line": line, "package": name, "workspace": False,
        })
        attribute = keys[0] if suffix else keys[1] if len(keys) > 1 else None
        if attribute == "package" and values:
            record["package"] = values[0]
        elif attribute == "workspace":
            record["workspace"] = values == ["true"]
        for n in range(len(values) - 2):
            if values[n + 1] == "=":
                if values[n] == "package":
                    record["package"] = values[n + 2]
                elif values[n] == "workspace":
                    record["workspace"] = values[n + 2] == "true"
    return [(name, record) for (_, name), record in records.items()]


def main():
    errors = []
    workspace = dict(dependencies(pathlib.Path("Cargo.toml")))
    for crate in sorted(pathlib.Path("crates").glob("*")):
        if not crate.is_dir() or crate.name == "environment":
            continue
        manifest = crate / "Cargo.toml"
        for name, dep in dependencies(manifest):
            package = dep["package"]
            if dep["workspace"]:
                package = workspace.get(name, {}).get("package", package)
            if is_actix(name) or is_actix(package):
                errors.append((manifest, dep["line"], "Actix dependency is forbidden in business feature crates"))
        src = crate / "src"
        for path in sorted(src.rglob("*.rs")):
            relative = path.relative_to(src)
            inner = relative.parts[0] in ("domain", "application", "domain.rs", "application.rs")
            for line, violation in source_violations(path, inner):
                errors.append((path, line, violation))
    for path, line, violation in errors:
        print(f"{path.as_posix()}:{line}: {violation}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
