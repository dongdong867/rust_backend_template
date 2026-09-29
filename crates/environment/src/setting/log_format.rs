/// How log lines are written: `pretty` for people, `json` for log collectors.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogFormat {
    Pretty,
    Json,
}
