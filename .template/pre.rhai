// Internal source rendering is used only by the twin consistency check, never prompted.
if !variable::is_set("template_source") {
    variable::set("template_source", false);
} else if variable::get("template_source").to_string() != "true" {
    abort("template_source=true is reserved for the canonical twin check");
}

// The service owns both its directory and default binary identity. Reject graph/path
// collisions before mutating the temporary source, not later in Cargo or file renaming.
let name = variable::get("project-name");
if name == "environment" || name == "migrate"
    || (name == "tasks" && variable::get("include_tasks"))
    || (name != "rust-backend-template" && file::exists(name)) {
    abort("Reserved service name: " + name
        + ". Choose a name distinct from retained packages, the migration binary and root paths.");
}

// Resolve the dependency graph from the final manifests, not the source repository's lock.
file::delete("Cargo.lock");

if !variable::get("include_tasks") {
    for path in [
        "crates/tasks",
        "migrations/0001_create_tasks.sql",
        "rust-backend-template/src/api/route/v1/tasks.rs",
        "rust-backend-template/src/api/route/v1",
        "rust-backend-template/src/api/route/v1.rs",
        "rust-backend-template/tests/tasks.rs",
        "rust-backend-template/tests/tasks.rs.liquid",
    ] {
        file::delete(path);
    }
}
