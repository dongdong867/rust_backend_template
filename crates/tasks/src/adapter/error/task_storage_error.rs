#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskStorageError {
    NotFound,
    AlreadyCompleted,
    Persistence,
}
