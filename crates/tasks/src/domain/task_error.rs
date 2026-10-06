#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskError {
    InvalidTitle,
    AlreadyCompleted,
    InvalidState,
}
