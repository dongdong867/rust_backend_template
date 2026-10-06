#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskRepositoryError {
    NotFound,
    AlreadyCompleted,
    Persistence,
}
