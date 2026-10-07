let name = variable::get("project-name");
if name != "rust-backend-template" {
    file::rename("rust-backend-template", name);
}

system::command("cargo", ["generate-lockfile"]);
// Crate renaming can change rustfmt's import ordering.
system::command("cargo", ["fmt", "--all"]);

// cargo-generate joins command arguments into a shell string; quote all dynamic paths.
fn shell_quote(value) {
    let escaped = value;
    escaped.replace("'", "'\"'\"'");
    "'" + escaped + "'"
}

// 0.25 also skips symlinks during its FINAL copy. Write the link directly in the output,
// after successful lock generation, rather than in the temporary rendering directory.
system::command("ln", [
    "-s", "AGENTS.md", shell_quote(env::destination_directory + "/CLAUDE.md")
]);
file::delete(".template");
